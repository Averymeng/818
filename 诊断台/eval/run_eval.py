#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E01-E17 评测执行器：真跑 + 硬断言自动判定 + 落 eval_case/eval_run + 结果 JSON

用法:
  python3 eval/run_eval.py --static           # 零成本用例（E13/E16/E17 + 已有报告可判的）
  python3 eval/run_eval.py --with-llm         # 完整：真跑 14 客户后跑全部断言
  python3 eval/run_eval.py --with-llm --cases E02,E08,E15   # 只跑指定客户（真跑部分）

前置: python3 eval/seed_cases.py（种案例，E15 依赖）
产物: eval/artifacts/{客户}.json（报告留档） + eval/artifacts/eval_results.json（逐用例断言结果）
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
AGENT = ROOT / "agent"
DATA = ROOT / "data"
DB = DATA / "ad_review.db"
ART = HERE / "artifacts"
ART.mkdir(exist_ok=True)
RESULT_PATH = ART / "eval_results.json"

sys.path.insert(0, str(AGENT))
from orchestrator import ReviewOrchestrator  # noqa: E402
import review_actions  # noqa: E402

# ---------------------------------------------------------------- 用例定义
# case_id -> (客户名, 是否真跑, 是否硬断言)   E01b/E10 真跑但纯软判断
REAL_CASES = {
    "E01":  ("码上AI学堂",    True),
    "E01b": ("环球语言村",     True),
    "E02":  ("悦颜美容SPA",   True),
    "E03":  ("银龄声乐课堂",  True),
    "E04":  ("拾光婚纱影像",  True),   # 第一次跑（前置于 E15）
    "E05":  ("星光KTV",       True),
    "E06":  ("启航留学工作室", True),
    "E07":  ("洁到家家政",    True),
    "E08":  ("云栖度假酒店",  True),
    "E09":  ("素人写真馆",    True),
    "E10":  ("巅峰密室",      True),   # 纯软判断
    "E11a": ("山野民宿",      True),
    "E11b": ("枕水人家客栈",  True),
    "E15":  ("拾光婚纱影像",  True),   # 第二次跑（case_ref_log 断言）
}

PLACEHOLDERS = ["（下钻未生成摘要）", "（待 LLM 归因）", "LLM 摘要待生成", "摘要待生成", "LLM 摘要", "待生成"]
POST_LINK_WORDS = ["ROI", "GMV", "成交额", "有效线索", "回访"]


# ---------------------------------------------------------------- 工具函数
def _text(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def top3_of(report):
    return report.get("chapters", {}).get("2_核心结论", {}).get("top3", []) or []


def watchlist_of(report):
    return report.get("chapters", {}).get("5_异常与原因", {}).get("watchlist", []) or []


def ch4_layers(report):
    return report.get("chapters", {}).get("4_分层诊断", []) or []


def trace_tools(conn, task_id):
    rows = conn.execute(
        "SELECT tool_name FROM agent_tool_call WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
    return [r["tool_name"] for r in rows]


def task_trend(report):
    return report.get("chapters", {}).get("3_指标与趋势", {}).get("trend_14d") or {}


def task_report(conn, task_id):
    r = conn.execute("SELECT report_json FROM report WHERE task_id=? ORDER BY id DESC LIMIT 1",
                     (task_id,)).fetchone()
    return json.loads(r["report_json"]) if r else None


def latest_verify(conn, task_id):
    r = conn.execute(
        """SELECT result_json FROM agent_tool_call
           WHERE task_id=? AND tool_name='verify_evidence' ORDER BY id DESC LIMIT 1""",
        (task_id,)).fetchone()
    return json.loads(r["result_json"]) if r else None


def real_run(customer, out_path):
    orch = ReviewOrchestrator(DB)
    try:
        r = orch.run(customer_name=customer, dry_run=False)
        tid = orch.task_id
    finally:
        orch.close()
    if "error" in r:
        return r, None
    out_path.write_text(_text(r), encoding="utf-8")
    return r, tid


def dry_run(customer):
    orch = ReviewOrchestrator(DB)
    try:
        r = orch.run(customer_name=customer, dry_run=True)
        tid = orch.task_id
    finally:
        orch.close()
    return r, tid


def fingerprint(conn, report, task_id):
    steps = [tuple(x) for x in conn.execute(
        "SELECT name, status FROM agent_step WHERE task_id=? ORDER BY seq", (task_id,))]
    tools = [r[0] for r in conn.execute(
        "SELECT tool_name FROM agent_tool_call WHERE task_id=? ORDER BY id", (task_id,))]
    core = [report["overall_status"],
            [e["location"] for e in top3_of(report)],
            steps, tools]
    return hashlib.sha1(_text(core).encode()).hexdigest()


def grep_code(pattern):
    hits = []
    for f in sorted(AGENT.glob("*.py")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(pattern, line, re.IGNORECASE):
                hits.append(f"{f.name}:{i}: {line.strip()}")
    return hits


# ---------------------------------------------------------------- 硬断言实现
def _a(case_id, name, ok, detail=""):
    ASSERTS.setdefault(case_id, []).append({"name": name, "ok": bool(ok), "detail": detail})


def run_static(conn):
    """零成本用例：E13 / E14(代码) / E16 / E17"""
    # ---- E13 版位排除 ----
    hits_vf = grep_code(r"video_feed")
    _a("E13", "代码无 video_feed 引用", not hits_vf, "; ".join(hits_vf) or "无")
    plc = [r[0] for r in conn.execute("SELECT DISTINCT placement FROM daily_metric")]
    plc_p = [r[0] for r in conn.execute("SELECT DISTINCT placement FROM plan")]
    _a("E13", "DB 版位仅 feed/search", set(plc + plc_p) <= {"feed", "search"},
       f"daily_metric={plc} plan={plc_p}")
    vids = []
    for f in sorted(ART.glob("*.json")):
        if f.name == "eval_results.json":
            continue
        t = f.read_text(encoding="utf-8")
        if "视频内流" in t:
            vids.append(f.name)
    _a("E13", "报告不出现'视频内流'", not vids, "; ".join(vids) or "无")

    # ---- E14 后链路排除（代码部分；报告部分待真跑后补）----
    hits_post = grep_code(r"\b(ROI|GMV)\b|成交额|有效线索")
    _a("E14", "代码无后链路计算", not hits_post, "; ".join(hits_post) or "无")
    posts = []
    for f in sorted(ART.glob("*.json")):
        if f.name == "eval_results.json":
            continue
        t = f.read_text(encoding="utf-8")
        for w in POST_LINK_WORDS:
            if w in t:
                posts.append(f"{f.name}->{w}")
    _a("E14", "报告不出现后链路结论", not posts, "; ".join(posts) or "无")

    # ---- E16 审核回流（临时库，不污染正式库）----
    tmpdir = tempfile.mkdtemp(prefix="eval_e16_")
    tmpdb = os.path.join(tmpdir, "tmp.db")
    tcon = sqlite3.connect(tmpdb)
    schema = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    tcon.executescript(schema)
    tcon.execute("INSERT INTO customer(name, sector_id, optimize_target, target_cost) VALUES ('测试客户',1,'open',20)")
    cid = tcon.execute("SELECT id FROM customer").fetchone()[0]
    tcon.execute(
        """INSERT INTO review_task(customer_id, task_type, cur_start, cur_end, cmp_start, cmp_end, status, sim_version)
           VALUES (?, 'weekly','2026-08-10','2026-08-16','2026-08-03','2026-08-09','succeeded','sim-v1.0.0')""",
        (cid,))
    tid = tcon.execute("SELECT id FROM review_task").fetchone()[0]
    tcon.execute(
        "INSERT INTO report(task_id, version, status, schema_version, report_json, sim_version) VALUES (?,1,'draft','report-v1','{}','sim-v1.0.0')",
        (tid,))
    rid = tcon.execute("SELECT id FROM report").fetchone()[0]
    tcon.execute(
        "INSERT INTO action_item(report_id, task_id, suggestion, priority, status) VALUES (?,?,?,?,?)",
        (rid, tid, "测试建议", "P0", "待沟通"))
    aid = tcon.execute("SELECT id FROM action_item").fetchone()[0]
    tcon.commit()

    r1 = review_actions.approve_report(tcon, rid)
    row = tcon.execute("SELECT action FROM report_review WHERE report_id=? ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
    st = tcon.execute("SELECT status FROM report WHERE id=?", (rid,)).fetchone()[0]
    _a("E16", "approve 落表 confirm 且状态 reviewed",
       r1.get("ok") and row[0] == "confirm" and st == "reviewed", f"action={row[0]} status={st}")

    r2 = review_actions.reject_report(tcon, rid, content_after="{\"修正\":true}", reason="成本口径用错")
    rr = tcon.execute("SELECT action, content_before, content_after FROM report_review WHERE report_id=? ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
    _a("E16", "reject(edit) 落表且前后内容留痕",
       r2.get("ok") and rr[0] == "edit" and rr[1] is not None and rr[2] is not None,
       f"action={rr[0]} before={bool(rr[1])} after={bool(rr[2])}")

    ok_bf = True
    for disp in ("keep", "modify", "reject"):
        rb = review_actions.record_backflow(tcon, aid, disp, actual_action=f"动作{disp}",
                                            result_7d={"target_cost_delta": -0.12, "spend_delta": 0.2, "improved": True})
        if not rb.get("ok"):
            ok_bf = False
    n_bf = tcon.execute("SELECT COUNT(*) FROM backflow WHERE action_item_id=?", (aid,)).fetchone()[0]
    _a("E16", "backflow 三态落表", ok_bf and n_bf == 3, f"rows={n_bf}")
    tcon.close()

    # ---- E17 复现性（码上 dry-run ×2）----
    r1, t1 = dry_run("码上AI学堂")
    r2, t2 = dry_run("码上AI学堂")
    fp1, fp2 = fingerprint(conn, r1, t1), fingerprint(conn, r2, t2)
    _a("E17", "dry-run ×2 指纹一致", fp1 == fp2,
       f"overall={r1['overall_status']}/{r2['overall_status']} fp={fp1[:8]}")


def run_reports_asserts(conn):
    """复用已真跑报告做断言（E01-E12/E15 硬断言）。需先有 artifacts。"""
    def load(case):
        f = ART / f"{REAL_CASES[case][0]}.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    # ---- E01 正常盘 ----
    r = load("E01")
    if r:
        _a("E01", "overall=正常", r["overall_status"] == "正常", r["overall_status"])
        _a("E01", "top3 空", top3_of(r) == [], _text(top3_of(r)))
        ch = r.get("chapters", {})
        expected_ch = ["1_封面", "2_核心结论", "3_指标与趋势", "4_分层诊断",
                       "5_异常与原因", "6_案例参考", "7_优化建议", "8_行动计划"]
        _a("E01", "八章节齐全", all(k in ch for k in expected_ch), ",".join(ch.keys()))
        t = _text(r)
        ph = [p for p in PLACEHOLDERS if p in t]
        _a("E01", "无占位符", not ph, "; ".join(ph) or "无")

    # ---- E02 消耗下降 ----
    r = load("E02")
    if r:
        _a("E02", "overall=需行动", r["overall_status"] == "需行动", r["overall_status"])
        t3 = top3_of(r)
        _a("E02", "top3 含 客户整体/消耗",
           any(e["location"] == "客户整体/消耗" for e in t3), _text(t3))
        plan_lay = [l for l in ch4_layers(r) if l.get("layer") == "plan"]
        _a("E02", "基建双门槛触发(plan层=显著)",
           bool(plan_lay) and plan_lay[0].get("status") == "显著",
           _text(plan_lay[0]) if plan_lay else "无 plan 层")

    # ---- E03 CPM上涨 ----
    r = load("E03")
    if r:
        t3 = top3_of(r)
        _a("E03", "top3 含 版位 feed/CPM",
           any("feed" in e["location"] and "CPM" in e["location"] for e in t3), _text(t3))

    # ---- E04 CTR下降（拾光第一次）----
    r = load("E04")
    if r:
        wl = watchlist_of(r)
        _a("E04", "CTR 事件被检出(观察项)",
           any("CTR" in e.get("location", "") for e in wl), _text(wl))
        t3 = top3_of(r)
        _a("E04", "CTR 不进 top3（权重设计预期）",
           not any("CTR" in e["location"] for e in t3), _text(t3))

    # ---- E05 按钮率下降 ----
    r = load("E05")
    if r:
        wl = watchlist_of(r)
        _a("E05", "button_rate 事件被检出",
           any("button_rate" in e.get("location", "") or "按钮" in e.get("location", "") for e in wl),
           _text(wl))

    # ---- E06 开口率下降 ----
    r = load("E06")
    if r:
        # 事件可能进 top3（weight 前3）或观察项（weight 4+），两个集合都要查
        pool = top3_of(r) + watchlist_of(r)
        _a("E06", "open_rate 事件被检出",
           any("open_rate" in e.get("location", "") or "开口率" in e.get("location", "") for e in pool),
           f"top3={_text(top3_of(r))[:200]} wl={_text(watchlist_of(r))[:200]}")

    # ---- E07 留资率下降 ----
    r = load("E07")
    if r:
        pool = top3_of(r) + watchlist_of(r)
        _a("E07", "lead_rate 事件被检出",
           any("lead_rate" in e.get("location", "") or "留资率" in e.get("location", "") for e in pool),
           f"top3={_text(top3_of(r))[:200]} wl={_text(watchlist_of(r))[:200]}")

    # ---- E08 复合成本上涨 ----
    r = load("E08")
    if r:
        t3 = top3_of(r)
        _a("E08", "lead_cost 进 top3",
           any("lead_cost" in e["location"] for e in t3), _text(t3))

    # ---- E09 正向 ----
    r = load("E09")
    if r:
        _a("E09", "overall=需关注", r["overall_status"] == "需关注", r["overall_status"])
        t3 = top3_of(r)
        # 正向语义 = 消耗升 + 成本降。成本事件 direction=negative（数值下降）反而是好事
        _a("E09", "top3 组合符合正向(消耗升+成本降)",
           bool(t3)
           and any(e.get("location") == "客户整体/消耗" and e["direction"] == "positive" for e in t3)
           and all(e["direction"] == "positive" or "cost" in e["location"] for e in t3),
           _text(t3))

    # ---- E11a/E11b 数据缺失 ----
    for case, name in (("E11a", "山野民宿"), ("E11b", "枕水人家客栈")):
        r = load(case)
        if not r:
            continue
        _a(case, "overall=需行动", r["overall_status"] == "需行动", r["overall_status"])
        _a(case, "top3 空", top3_of(r) == [], _text(top3_of(r)))
        ch = r.get("chapters", {})
        summ = _text(ch.get("2_核心结论", {}).get("summary", ""))
        _a(case, "拒绝归因(摘要声明数据不足)",
           any(k in summ for k in ("数据不足", "缺口", "不完整", "无法归因")), summ[:60])
        detail = ch.get("5_异常与原因", {}).get("top3_detail", "")
        _a(case, "不输出 top3 归因", detail in ("", [], None), _text(detail)[:60])
        miss = ch.get("2_核心结论", {}).get("data_check", {})
        miss_all = (miss.get("cur_missing") or []) + (miss.get("prev_missing") or [])
        _a(case, "标注数据缺口", bool(miss_all),
           f"cur={miss.get('cur_missing')} prev={miss.get('prev_missing')}")

    # ---- E12 口径切换（复用 E03/E04 报告）----
    r3, r4 = load("E03"), load("E04")
    if r3 and r4:
        m3 = task_trend(r3).get("metric")
        m4 = task_trend(r4).get("metric")
        _a("E12", "银龄(open)用 open_cost 口径", m3 == "open_cost", f"metric={m3}")
        _a("E12", "拾光(lead)用 lead_cost 口径", m4 == "lead_cost", f"metric={m4}")


def run_e15(conn):
    """E15 案例引用治理：取拾光最近一次 task 断言"""
    tids = [r[0] for r in conn.execute(
        """SELECT id FROM review_task WHERE customer_id=
           (SELECT id FROM customer WHERE name='拾光婚纱影像') ORDER BY id DESC LIMIT 2""")]
    if not tids:
        return
    tid = tids[0]
    tools = trace_tools(conn, tid)
    _a("E15", "search_cases 被调用", "search_cases" in tools, ",".join(tools[:12]))
    sc = conn.execute(
        """SELECT result_json FROM agent_tool_call WHERE task_id=? AND tool_name='search_cases' ORDER BY id DESC LIMIT 1""",
        (tid,)).fetchone()
    cases = []
    if sc:
        cases = json.loads(sc["result_json"]).get("cases", [])
    _a("E15", "引用的案例全部 referenceable=1 且非 badcase",
       bool(cases) and all(c.get("referenceable") == 1 and c.get("status") != "badcase" for c in cases),
       f"n={len(cases)} statuses={[c.get('status') for c in cases]}")
    _a("E15", "badcase 不出现", all(c.get("status") != "badcase" for c in cases),
       f"n={len(cases)}")
    nlog = conn.execute("SELECT COUNT(*) FROM case_ref_log WHERE task_id=?", (tid,)).fetchone()[0]
    _a("E15", "case_ref_log 留痕", nlog > 0, f"rows={nlog}")


def run_e11b_trend(conn):
    """E11b 趋势线如实呈现缺口（独立函数，报告加载失败也不影响）"""
    f = ART / "枕水人家客栈.json"
    if not f.exists():
        return
    r = json.loads(f.read_text(encoding="utf-8"))
    trend = task_trend(r)
    daily = trend.get("daily", []) or []
    _a("E11b", "趋势线如实呈现缺口(点数<14)", len(daily) < 14, f"points={len(daily)}")


# ---------------------------------------------------------------- 主流程
ASSERTS = {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-llm", action="store_true", help="真跑 LLM 用例")
    ap.add_argument("--cases", help="指定真跑用例，逗号分隔，如 E02,E08")
    args = ap.parse_args()

    # 前置检查：案例库（E15 依赖）
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    ncase = conn.execute("SELECT COUNT(*) FROM diag_case").fetchone()[0]
    if ncase == 0:
        print("警告：diag_case 为空，E15 断言必然失败。先执行 python3 eval/seed_cases.py")
    if ncase < 2:
        print("警告：diag_case 不足 2 条（需要 1 reference + 1 badcase），请重新 seed_cases.py")

    # 真跑阶段
    if args.with_llm:
        targets = list(REAL_CASES.keys())
        if args.cases:
            targets = [c.strip() for c in args.cases.split(",") if c.strip() in REAL_CASES]
        for case in targets:
            customer = REAL_CASES[case][0]
            f = ART / f"{customer}.json"
            if case != "E15" and f.exists() and not args.cases:
                print(f"[skip] {case} {customer} 已有报告")
                continue
            print(f"[run ] {case} {customer} ...", flush=True)
            r, tid = real_run(customer, f)
            if "error" in r:
                print(f"  !! 失败: {r['error']}", flush=True)
            else:
                print(f"  overall={r['overall_status']} llm_calls={r['llm_calls']} "
                      f"cost=¥{r.get('llm_cost_yuan', 0):.4f} task={tid}", flush=True)
        print("真跑完成。", flush=True)

    # 静态用例（E13/E16/E17）
    run_static(conn)

    # 报告断言（依赖 artifacts）
    run_reports_asserts(conn)
    run_e11b_trend(conn)
    run_e15(conn)

    # ---- 汇总 ----
    passed = sum(1 for a in ASSERTS.values() for x in a if x["ok"])
    total = sum(len(a) for a in ASSERTS.values())
    print(f"\n硬断言: {passed}/{total} 通过")

    # ---- 落 eval_case / eval_run ----
    ev_cases = []
    for case in list(REAL_CASES.keys()) + ["E12", "E13", "E14", "E16", "E17"]:
        if case in REAL_CASES:
            name = REAL_CASES[case][0]
            cid = conn.execute("SELECT id FROM customer WHERE name=?", (name,)).fetchone()
            ev_cases.append((case, name, cid["id"] if cid else None))
        else:
            ev_cases.append((case, case, None))
    for case, name, cid in ev_cases:
        conn.execute(
            """INSERT INTO eval_case(name, scenario, sim_version, customer_id, expected_json)
               VALUES (?,?,?,?,?)""",
            (case, "见评测集设计稿", "sim-v1.0.0", cid,
             json.dumps({"asserts": ASSERTS.get(case, [])}, ensure_ascii=False)))
    scores = {k: {"pass": sum(1 for x in v if x["ok"]), "total": len(v), "asserts": v}
              for k, v in ASSERTS.items()}
    conn.execute(
        """INSERT INTO eval_run(agent_version, eval_set_version, scores_json, passed, ran_at)
           VALUES (?,?,?,?,datetime('now','localtime'))""",
        ("agent-v1.1", "E01-E17-v1",
         json.dumps(scores, ensure_ascii=False), passed == total))
    conn.commit()

    RESULT_PATH.write_text(json.dumps({
        "passed": passed, "total": total, "scores": scores,
        "report_files": {c: f"{name}.json" for c, (name, _) in REAL_CASES.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入: {RESULT_PATH}")
    conn.close()


if __name__ == "__main__":
    main()
