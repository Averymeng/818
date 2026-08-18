# -*- coding: utf-8 -*-
"""
诊断台 · 9 项派生指标口径（唯一权威来源，所有工具共用）
比例类指标一律用汇总分子/汇总分母加权计算（口径见交接文档 4.2/4.3）
"""

BASE_COLS = {
    "spend": "SUM(spend)",
    "impressions": "SUM(impressions)",
    "note_clicks": "SUM(note_clicks)",
    "button_clicks": "SUM(button_clicks)",
    "open_msg": "SUM(open_msg)",
    "lead_cnt": "SUM(lead_cnt)",
}


def safe_div(a, b):
    try:
        if b in (0, None) or a is None:
            return None
        return a / b
    except Exception:
        return None


def derive_metrics(m):
    """m: 含 spend/impressions/note_clicks/button_clicks/open_msg/lead_cnt 的 dict → 9 项派生指标"""
    return {
        "CPM": round(safe_div(m["spend"], m["impressions"]) * 1000, 2) if safe_div(m["spend"], m["impressions"]) is not None else None,
        "CTR": safe_div(m["note_clicks"], m["impressions"]),
        "CPC": safe_div(m["spend"], m["note_clicks"]),
        "button_rate": safe_div(m["button_clicks"], m["note_clicks"]),
        "open_rate": safe_div(m["open_msg"], m["button_clicks"]),
        "lead_rate": safe_div(m["lead_cnt"], m["open_msg"]),
        "lead_cvr": safe_div(m["lead_cnt"], m["note_clicks"]),
        "open_cost": safe_div(m["spend"], m["open_msg"]),
        "lead_cost": safe_div(m["spend"], m["lead_cnt"]),
    }


def pct_change(cur, prev):
    """环比变化率；prev 无效时返回 None（不得臆断）"""
    if prev is None or prev == 0 or cur is None:
        return None
    return (cur - prev) / prev


def agg_row(row):
    """sqlite 行 → 汇总 dict（键名标准化）"""
    keys = ["spend", "impressions", "note_clicks", "button_clicks", "open_msg", "lead_cnt"]
    return {k: (row[k] or 0) for k in keys}
