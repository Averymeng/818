# -*- coding: utf-8 -*-
"""
诊断台 · 审核与回流最小落表接口（数据层，非前端；E16 评测用）

功能（对应 schema report_review / backflow / report）:
  approve_report   审核通过：report_review 落 confirm（section_key=NULL=全局审核），report.status→reviewed
  reject_report    驳回/修改：report_review 落 edit/reject，留 content_before/content_after 内容痕迹
  record_backflow  7 天后回流：backflow 落 keep/modify/reject + 实际动作 + 7天结果
  promote_to_case  审核通过的报告沉淀为参考案例（diag_case），打通案例库/RAG 飞轮
  record_badcase   驳回时把缺陷记入 diag_badcase（status='open'，等修复后转 fixed）

约定:
  report_review.action ∈ confirm/edit/reject/insufficient（schema CHECK）
  backflow.disposition ∈ keep/modify/reject（schema CHECK，仅三项回流）
  本模块只做落表，不做业务校验；前端阶段复用同一接口
"""
import json

# 签名归一化唯一来源：从 tools 复用 SIG_MAP_EN2CN / norm_sig_term，
# 保证入库签名与 search_cases 召回口径一致（避免 open_cost/lead_cost 类静默漏匹配）。
import tools as _tools
_SIG_MAP = _tools.SIG_MAP_EN2CN
_norm_sig = _tools.norm_sig_term


def _resolve_report(conn, report_id):
    return conn.execute("SELECT id, status FROM report WHERE id=?", (report_id,)).fetchone()


def approve_report(conn, report_id, reviewer="sales"):
    """审核通过：report_review 落 confirm（全局审核），report.status→reviewed"""
    row = _resolve_report(conn, report_id)
    if not row:
        return {"error": f"report {report_id} 不存在"}
    conn.execute(
        "INSERT INTO report_review(report_id, section_key, action, reason, reviewer) VALUES (?,NULL,'confirm',?,?)",
        (report_id, "全局审核通过，可进入执行", reviewer))
    conn.execute(
        "UPDATE report SET status='reviewed', updated_at=datetime('now','localtime') WHERE id=?", (report_id,))
    conn.commit()
    return {"ok": True, "report_id": report_id, "action": "confirm", "new_status": "reviewed"}


def reject_report(conn, report_id, content_after=None, reason="", reviewer="sales"):
    """驳回/修改：content_after 给修改后内容视为 edit，否则 reject；前后内容留痕"""
    row = _resolve_report(conn, report_id)
    if not row:
        return {"error": f"report {report_id} 不存在"}
    action = "edit" if content_after else "reject"
    cur = conn.execute("SELECT report_json FROM report WHERE id=?", (report_id,)).fetchone()[0]
    conn.execute(
        """INSERT INTO report_review(report_id, section_key, action, content_before, content_after, reason, reviewer)
           VALUES (?,NULL,?,?,?,?,?)""",
        (report_id, action, cur, content_after or None, reason, reviewer))
    conn.execute(
        "UPDATE report SET status='draft', updated_at=datetime('now','localtime') WHERE id=?", (report_id,))
    conn.commit()
    return {"ok": True, "report_id": report_id, "action": action}


def record_backflow(conn, action_item_id, disposition, actual_action="", result_7d=None):
    """7 天后回流：disposition∈keep/modify/reject；result_7d={target_cost_delta, spend_delta, improved}"""
    row = conn.execute("SELECT id FROM action_item WHERE id=?", (action_item_id,)).fetchone()
    if not row:
        return {"error": f"action_item {action_item_id} 不存在"}
    if disposition not in ("keep", "modify", "reject"):
        return {"error": f"disposition 必须为 keep/modify/reject，收到 {disposition}"}
    conn.execute(
        "INSERT INTO backflow(action_item_id, disposition, actual_action, result_7d_json) VALUES (?,?,?,?)",
        (action_item_id, disposition, actual_action,
         json.dumps(result_7d or {}, ensure_ascii=False)))
    conn.commit()
    return {"ok": True, "action_item_id": action_item_id, "disposition": disposition}


def promote_to_case(conn, report_id):
    """审核通过的报告沉淀为参考案例（diag_case）——案例库/RAG 飞轮的入库口。

    提取逻辑（全部确定性，不调 LLM）：
      - 客户维度：customer → sector → industry，品类取 customer_category 第一条
      - anomaly_signature：优先从 report_json 第 5 章 top3_detail 取 location 拼接
        （报告 JSON 是用户实际审核的内容，最可靠）；为空再查 anomaly 表；都没有则记"正常周"
      - key_evidence_json：Top3 异常的 rank/location/reason/evidence
      - action_taken：第 8 章行动计划 + 第 7 章建议摘要（审核时点尚未执行，result_after 留空待回流）
    幂等：同一 report_id、或同一客户+相同异常签名，重复调用只入库一次
    （防止「重新生成报告→新 report_id→再次 promote」造成的案例库重复）。
    """
    ex = conn.execute("SELECT id FROM diag_case WHERE source_report_id=?", (report_id,)).fetchone()
    if ex:
        return {"ok": True, "case_id": ex["id"], "duplicated": True, "signature": "(该报告已入过案例库)"}
    row = conn.execute(
        """SELECT r.task_id, r.report_json, rt.customer_id FROM report r
           JOIN review_task rt ON rt.id = r.task_id WHERE r.id=?""", (report_id,)).fetchone()
    if not row:
        return {"error": f"report {report_id} 不存在"}
    task_id, customer_id = row["task_id"], row["customer_id"]

    cust = conn.execute(
        """SELECT c.id, c.sector_id, s.industry_id, c.optimize_target, s.name sector_name
           FROM customer c JOIN sector s ON s.id=c.sector_id WHERE c.id=?""", (customer_id,)).fetchone()
    if not cust:
        return {"error": f"customer {customer_id} 不存在"}
    cat = conn.execute(
        "SELECT category_id FROM customer_category WHERE customer_id=? ORDER BY category_id LIMIT 1",
        (customer_id,)).fetchone()

    # --- 从 report_json 提取异常与行动（权威来源） ---
    top3, actions = [], []
    try:
        rj = json.loads(row["report_json"]) if row["report_json"] else {}
        ch = rj.get("chapters", {}) if isinstance(rj, dict) else {}
        top3 = ch.get("5_异常与原因", {}).get("top3_detail") or []
        # 兼容：空壳报告 top3_detail 可能是占位字符串（如"待 LLM 归因"）
        if isinstance(top3, str):
            top3 = []
        top3 = [t for t in top3 if isinstance(t, dict)]
        plan = ch.get("8_行动计划", []) or []
        sugg = ch.get("7_优化建议", []) or []
        if isinstance(plan, str):
            plan = []
        if isinstance(sugg, str):
            sugg = []
        actions = [a.get("action", "") for a in plan if isinstance(a, dict) and a.get("action")]
        actions += [s.get("text", "") for s in sugg if isinstance(s, dict) and s.get("text")]
    except Exception:
        top3, actions = [], []

    # --- anomaly 表兜底（真实跑全流程时才有行） ---
    if not top3:
        anoms = conn.execute(
            """SELECT location, direction, magnitude, impact_spend, impact_cost, rank
               FROM anomaly WHERE task_id=? AND is_top3=1 ORDER BY rank LIMIT 3""",
            (task_id,)).fetchall()
        top3 = [{"rank": a["rank"], "location": a["location"], "reason": "",
                 "evidence": [], "direction": a["direction"]} for a in anoms]

    if top3:
        signature = " + ".join(_norm_sig(str(t.get("location", ""))) for t in top3 if t.get("location"))
    else:
        signature = "无明显异常（正常周）"
    # 去重（根因 A 修复）：同一客户 + 相同异常签名不再重复入库。
    # 仅按 source_report_id 去重不足以防重复——重新生成报告会产生新的 report_id，
    # 导致同一客户被多次 promote。这里用 customer_id+anomaly_signature 兜底。
    ex2 = conn.execute(
        "SELECT id FROM diag_case WHERE customer_id=? AND anomaly_signature=?",
        (customer_id, signature)).fetchone()
    if ex2:
        return {"ok": True, "case_id": ex2["id"], "duplicated": True,
                "signature": signature,
                "note": "该客户已有相同异常签名的案例，未重复入库"}
    evidence = [{"rank": t.get("rank"), "location": t.get("location"),
                 "reason": t.get("reason", ""), "evidence": t.get("evidence", [])} for t in top3]
    action_taken = "；".join(a for a in actions if a) or None

    cur = conn.execute(
        "INSERT INTO diag_case(source_report_id, customer_id, industry_id, sector_id, category_id,"
        " optimize_target, anomaly_signature, key_evidence_json, action_taken, status, referenceable)"
        " VALUES (?,?,?,?,?,?,?,?,?, 'reference', 1)",
        (report_id, customer_id, cust["industry_id"], cust["sector_id"],
         cat["category_id"] if cat else None, cust["optimize_target"], signature,
         json.dumps(evidence, ensure_ascii=False, default=str), action_taken))
    conn.commit()
    return {"ok": True, "case_id": cur.lastrowid, "signature": signature,
            "n_evidence": len(evidence), "n_actions": len(actions)}


def record_badcase(conn, report_id, title, category=None, root_cause=None, red_line_fix=None):
    """驳回报告时把缺陷记入 diag_badcase（status='open'），沉淀「踩坑→根因→修复」闭环的入口。

    title 必填（一句话缺陷描述，通常取用户驳回理由）；修复后由人工/脚本改 status='fixed'。
    幂等：同一 report_id 不重复记（驳回可能多次，留第一次的理由即可）。
    """
    if not (title or "").strip():
        return {"error": "badcase title（驳回理由）不能为空"}
    ex = conn.execute("SELECT id FROM diag_badcase WHERE source_report_id=?", (report_id,)).fetchone()
    if ex:
        return {"ok": True, "badcase_id": ex["id"], "duplicated": True}
    row = conn.execute(
        """SELECT rt.customer_id FROM report r JOIN review_task rt ON rt.id=r.task_id
           WHERE r.id=?""", (report_id,)).fetchone()
    if not row:
        return {"error": f"report {report_id} 不存在"}
    cur = conn.execute(
        "INSERT INTO diag_badcase(source_report_id, customer_id, title, category,"
        " error_output, root_cause, red_line_fix, status) VALUES (?,?,?,?,?,?,?, 'open')",
        (report_id, row["customer_id"], title.strip(), category, None, root_cause, red_line_fix))
    conn.commit()
    return {"ok": True, "badcase_id": cur.lastrowid}
