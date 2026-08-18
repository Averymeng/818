# -*- coding: utf-8 -*-
"""
诊断台 · 审核与回流最小落表接口（数据层，非前端；E16 评测用）

功能（对应 schema report_review / backflow / report）:
  approve_report   审核通过：report_review 落 confirm（section_key=NULL=全局审核），report.status→reviewed
  reject_report    驳回/修改：report_review 落 edit/reject，留 content_before/content_after 内容痕迹
  record_backflow  7 天后回流：backflow 落 keep/modify/reject + 实际动作 + 7天结果

约定:
  report_review.action ∈ confirm/edit/reject/insufficient（schema CHECK）
  backflow.disposition ∈ keep/modify/reject（schema CHECK，仅三项回流）
  本模块只做落表，不做业务校验；前端阶段复用同一接口
"""
import json


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
