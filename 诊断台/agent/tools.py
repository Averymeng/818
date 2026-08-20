# -*- coding: utf-8 -*-
"""
诊断台 · Agent 工具目录（交接文档 9.2，四类共 14 个条目）
  A 确定性计算/查询（7）：get_customer_profile / get_daily_metrics / get_period_comparison
                          / get_funnel / get_trend / get_infrastructure / check_data_completeness
  B 规则引擎（1）：detect_anomalies
  C 下钻（3）：split_by_dimension / compare_new_old / drill_down_object
  D RAG与产出（3）：search_cases / verify_evidence / assemble_report

设计约束：
  - 全部纯代码、确定性、只读（无任何账户操作类工具）
  - 比例指标加权汇总；版位占比用 pp；LLM 永不心算，数字一律来自本目录
  - 每次调用经 ToolTracer 落库 agent_tool_call（见 orchestrator.py）
"""
import json
import re
import sqlite3
from datetime import date, datetime, timedelta

from metrics import derive_metrics, pct_change, agg_row

# 报告文本归一化：修正维度英文名、贡献度小数、delta 单位，保证展示一致
PLACEMENT_CN = {"feed": "信息流", "search": "搜索"}
# 指标英文名→中文（用于归一化 LLM/摘要文本，避免正文残留 open_cost/lead_cost 等）
METRIC_TEXT_CN = {"open_cost": "开口成本", "lead_cost": "留资成本"}
_CONTRIB_RE = re.compile(r"贡献度\s*(\d+(?:\.\d+)?)(?!%)")
_DELTA_RE = re.compile(r"delta\s*([+-]?\d[\d.]+)")


def norm_report_text(s):
    """归一化 LLM / 原始维度名文本：维度英文名→中文、贡献度小数→百分数、delta→带货币单位。"""
    if not isinstance(s, str):
        return s
    for en, cn in PLACEMENT_CN.items():
        s = s.replace(en, cn)
    for en, cn in METRIC_TEXT_CN.items():
        s = s.replace(en, cn)
    s = _CONTRIB_RE.sub(lambda m: "贡献度{:.2f}%".format(float(m.group(1)) * 100), s)
    s = _DELTA_RE.sub(lambda m: "变化 ¥{}".format(m.group(1)), s)
    return s


def norm_top3(item):
    """归一化 Top3 异常条目（location / reason / evidence 文本）。"""
    if not isinstance(item, dict):
        return item
    item = dict(item)
    if item.get("location"):
        item["location"] = norm_report_text(item["location"])
    if item.get("reason"):
        item["reason"] = norm_report_text(item["reason"])
    if isinstance(item.get("evidence"), list):
        item["evidence"] = [norm_report_text(e) for e in item["evidence"]]
    return item

DB_PATH_DEFAULT = None  # 由调用方注入


# ---------------------------------------------------------------- 基础设施
def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _d(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def _drange(start, end):
    d0, d1 = _d(start), _d(end)
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def _base_where(customer_id, start, end, extra=""):
    return ("customer_id=? AND date>=? AND date<=? " + extra), (customer_id, start, end)


# ---------------------------------------------------------------- A 类
def get_customer_profile(conn, customer_id):
    """customer_id → 行业/赛道/品类/优化目标/目标成本"""
    r = conn.execute(
        """SELECT c.id, c.name, c.optimize_target, c.target_cost,
                  i.name AS industry, s.name AS sector
           FROM customer c JOIN sector s ON c.sector_id=s.id
                JOIN industry i ON s.industry_id=i.id
           WHERE c.id=?""", (customer_id,)).fetchone()
    if not r:
        return {"error": f"customer {customer_id} 不存在"}
    cats = [x["name"] for x in conn.execute(
        """SELECT k.name FROM category k
           JOIN customer_category cc ON cc.category_id=k.id
           WHERE cc.customer_id=?""", (customer_id,))]
    return {
        "customer_id": r["id"], "name": r["name"],
        "industry": r["industry"], "sector": r["sector"],
        "categories": cats,
        "optimize_target": r["optimize_target"],      # open / lead
        "target_cost": r["target_cost"],
    }


def _agg(conn, customer_id, start, end, group_sql=None, params_extra=()):
    """按可选维度汇总原始量。group_sql 形如 'placement' / 'plan_id' / 'note_id'"""
    cols = ", ".join(f"{v} AS {k}" for k, v in
                     [("spend", "SUM(spend)"), ("impressions", "SUM(impressions)"),
                      ("note_clicks", "SUM(note_clicks)"), ("button_clicks", "SUM(button_clicks)"),
                      ("open_msg", "SUM(open_msg)"), ("lead_cnt", "SUM(lead_cnt)")])
    gsel = f", {group_sql} AS dim" if group_sql else ""
    gby = f" GROUP BY {group_sql} ORDER BY spend DESC" if group_sql else ""
    sql = f"SELECT {cols}{gsel} FROM daily_metric WHERE customer_id=? AND date>=? AND date<=?{gby}"
    rows = conn.execute(sql, (customer_id, start, end) + params_extra).fetchall()
    out = [dict(agg_row(r), **({"dim": r["dim"]} if group_sql else {})) for r in rows]
    return out


def get_daily_metrics(conn, customer_id, start, end, dim=None):
    """客户+区间+维度(placement/plan/note/None) → 原始量 + 9 项指标（加权）"""
    col = {"placement": "placement", "plan": "plan_id", "note": "note_id"}.get(dim)
    rows = _agg(conn, customer_id, start, end, col)
    out = []
    for r in rows:
        item = dict(r)
        if col == "plan_id" and col:
            p = conn.execute("SELECT name, placement FROM plan WHERE id=?", (r["dim"],)).fetchone()
            if p:
                item["plan_name"], item["placement"] = p["name"], p["placement"]
        if col == "note_id":
            n = conn.execute("SELECT title, material_form FROM note WHERE id=?", (r["dim"],)).fetchone()
            if n:
                item["note_title"], item["material_form"] = n["title"], n["material_form"]
        item["metrics"] = derive_metrics(r)
        out.append(item)
    return {"customer_id": customer_id, "start": start, "end": end, "dim": dim, "rows": out}


def get_period_comparison(conn, customer_id, cur_start, cur_end, cmp_start, cmp_end, dim=None):
    """客户+本周+上周+维度 → 9 指标与消耗的环比（比例加权、版位占比 pp）"""
    col = {"placement": "placement", "plan": "plan_id", "note": "note_id"}.get(dim)
    cur, cmp_ = _agg(conn, customer_id, cur_start, cur_end, col), _agg(conn, customer_id, cmp_start, cmp_end, col)
    if col:  # 维度环比按对象对齐
        idx_c = {r["dim"]: r for r in cur}
        idx_p = {r["dim"]: r for r in cmp_}
        rows = []
        for k in sorted(set(idx_c) | set(idx_p)):
            c, p = idx_c.get(k), idx_p.get(k)
            mc, mp = derive_metrics(c) if c else {}, derive_metrics(p) if p else {}
            rows.append({
                "dim": k,
                "cur": {**({"spend": c["spend"]} if c else {})},
                "spend_delta": (c["spend"] - p["spend"]) if (c and p) else None,
                "metrics_cur": mc, "metrics_prev": mp,
                "metrics_change": {m: pct_change(mc.get(m), mp.get(m)) for m in
                                   ["CPM", "CTR", "CPC", "button_rate", "open_rate", "lead_rate", "lead_cvr", "open_cost", "lead_cost"]},
            })
        return {"customer_id": customer_id, "dim": dim, "rows": rows}

    def tot(rows):
        t = {k: 0 for k in ["spend", "impressions", "note_clicks", "button_clicks", "open_msg", "lead_cnt"]}
        for r in rows:
            for k in t:
                t[k] += r[k]
        return t
    tc, tp = tot(cur), tot(cmp_)
    mc, mp = derive_metrics(tc), derive_metrics(tp)
    # 版位占比变化（pp）
    share = {}
    for pl in ("feed", "search"):
        sc = sum(r["spend"] for r in cur if r.get("placement") == pl) if not col else 0
        share[pl] = None  # 客户级由 placement 维度单独调 get_period_comparison(dim='placement') 获得
    return {
        "customer_id": customer_id,
        "cur": tc, "prev": tp,
        "metrics_cur": mc, "metrics_prev": mp,
        "metrics_change": {m: pct_change(mc.get(m), mp.get(m)) for m in
                           ["CPM", "CTR", "CPC", "button_rate", "open_rate", "lead_rate", "lead_cvr", "open_cost", "lead_cost"]},
        "spend_change": pct_change(tc["spend"], tp["spend"]),
    }


def get_funnel(conn, customer_id, start, end, dim=None):
    """客户+区间+维度 → 漏斗各环节量与率"""
    col = {"placement": "placement", "plan": "plan_id", "note": "note_id"}.get(dim)
    rows = _agg(conn, customer_id, start, end, col)
    out = []
    for r in rows:
        t = {k: r[k] for k in ["impressions", "note_clicks", "button_clicks", "open_msg", "lead_cnt"]}
        out.append({
            "dim": r.get("dim"),
            "stages": t,
            "spend": r["spend"],
            "rates": {
                "CTR": t["note_clicks"] / t["impressions"] if t["impressions"] else None,
                "button_rate": t["button_clicks"] / t["note_clicks"] if t["note_clicks"] else None,
                "open_rate": t["open_msg"] / t["button_clicks"] if t["button_clicks"] else None,
                "lead_rate": t["lead_cnt"] / t["open_msg"] if t["open_msg"] else None,
            },
        })
    return {"customer_id": customer_id, "start": start, "end": end, "dim": dim, "rows": out}


def get_trend(conn, customer_id, metric, days, end=None):
    """客户+指标+天数 → 日趋势 + 14日均 + 28天加权基准"""
    if end is None:
        end = conn.execute("SELECT MAX(date) FROM daily_metric WHERE customer_id=?", (customer_id,)).fetchone()[0]
    start = (_d(end) - timedelta(days=days - 1)).isoformat()
    rows = conn.execute(
        """SELECT date, SUM(spend) spend, SUM(impressions) impressions, SUM(note_clicks) note_clicks,
                  SUM(button_clicks) button_clicks, SUM(open_msg) open_msg, SUM(lead_cnt) lead_cnt
           FROM daily_metric WHERE customer_id=? AND date>=? AND date<=? GROUP BY date ORDER BY date""",
        (customer_id, start, end)).fetchall()
    raw_keys = {"spend", "impressions", "note_clicks", "button_clicks", "open_msg", "lead_cnt"}
    daily = []
    for r in rows:
        base = derive_metrics(agg_row(r))
        raw = agg_row(r)
        val = base.get(metric) if metric in base else raw.get(metric)
        daily.append({"date": r["date"], "value": val})
    vals = [x["value"] for x in daily if x["value"] is not None]
    n14 = vals[-14:] if len(vals) >= 14 else vals
    n28 = vals[-28:] if len(vals) >= 28 else vals
    avg14 = sum(n14) / len(n14) if n14 else None
    base28 = sum(n28) / len(n28) if n28 else None  # 成本类后置加权：近端权重高
    if metric in ("CPM", "CPC", "open_cost", "lead_cost") and n28:
        w = [i + 1 for i in range(len(n28))]
        base28 = sum(v * wi for v, wi in zip(n28, w)) / sum(w)
    return {"customer_id": customer_id, "metric": metric, "end": end,
            "daily": daily, "avg_14d": avg14, "baseline_28d_weighted": base28}


def get_infrastructure(conn, customer_id, cur_start, cur_end, cmp_start, cmp_end):
    """客户+两区间 → 在投/新投计划与笔记 + 基建双门槛判断"""
    def counts(s, e):
        plans = conn.execute(
            """SELECT COUNT(*) n, SUM(CASE WHEN created_date>=? AND created_date<=? THEN 1 ELSE 0 END) new_n
               FROM plan WHERE customer_id=? AND status='在投'
                 AND (stopped_date IS NULL OR stopped_date>=? OR created_date<=?)""",
            (s, e, customer_id, e, s)).fetchone()
        notes = conn.execute(
            """SELECT COUNT(*) n, SUM(CASE WHEN created_date>=? AND created_date<=? THEN 1 ELSE 0 END) new_n
               FROM note WHERE customer_id=? AND status='在投'
                 AND (stopped_date IS NULL OR stopped_date>=? OR created_date<=?)""",
            (s, e, customer_id, e, s)).fetchone()
        # 实际有消耗的口径（以数据为准）
        act_p = conn.execute(
            """SELECT COUNT(DISTINCT plan_id) FROM daily_metric
               WHERE customer_id=? AND date>=? AND date<=? AND spend>0""", (customer_id, s, e)).fetchone()[0]
        act_n = conn.execute(
            """SELECT COUNT(DISTINCT note_id) FROM daily_metric
               WHERE customer_id=? AND date>=? AND date<=? AND spend>0""", (customer_id, s, e)).fetchone()[0]
        return {"plans": act_p, "notes": act_n,
                "new_plans": plans["new_n"] or 0, "new_notes": notes["new_n"] or 0}
    c, p = counts(cur_start, cur_end), counts(cmp_start, cmp_end)

    def gate(cur_n, prev_n):
        d = cur_n - prev_n
        rel = d / prev_n if prev_n else None
        return {"cur": cur_n, "prev": prev_n, "delta": d, "rel": rel,
                "hit": (abs(d) >= 2 and rel is not None and abs(rel) >= 0.2)}
    return {"customer_id": customer_id,
            "cur": c, "prev": p,
            "plan_gate": gate(c["plans"], p["plans"]),    # ≥2 个 且 ≥20%
            "note_gate": gate(c["notes"], p["notes"])}    # ≥5 篇 且 ≥20%


def check_data_completeness(conn, customer_id, start, end):
    """客户+区间 → 缺失日期/样本充足度；不过 → 全链路降级'数据不足'"""
    full = _drange(start, end)
    have = {r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_metric WHERE customer_id=? AND date>=? AND date<=?",
        (customer_id, start, end))}
    missing = [d for d in full if d not in have]
    zero_days = [r[0] for r in conn.execute(
        """SELECT date FROM daily_metric WHERE customer_id=? AND date>=? AND date<=?
           GROUP BY date HAVING SUM(spend)=0""", (customer_id, start, end))]
    # 样本充足度：周期≥7天无缺口
    ok = (len(full) >= 7) and (not missing) and (not zero_days)
    return {"customer_id": customer_id, "start": start, "end": end,
            "expected_days": len(full), "missing_days": missing, "zero_spend_days": zero_days,
            "sufficient": ok,
            "verdict": "pass" if ok else "insufficient"}


# ---------------------------------------------------------------- B 类
METRIC_LIST = ["CPM", "CTR", "CPC", "button_rate", "open_rate", "lead_rate", "lead_cvr", "open_cost", "lead_cost"]
ADVERSE = {"CPM": +1, "CPC": +1, "open_cost": +1, "lead_cost": +1,  # +1: 涨=不利
           "CTR": -1, "button_rate": -1, "open_rate": -1, "lead_rate": -1, "lead_cvr": -1}


def detect_anomalies(conn, customer_id, cur_start, cur_end, cmp_start, cmp_end,
                     metric_threshold=0.10, spend_threshold=0.15):
    """规则引擎：客户级 + 版位级环比扫描 → 异常事件（位置/方向/权重 40/30/20/10/Top3）
    默认阈值 指标±10% / 消耗±15%（周度口径；日监控阈值另有 ±20%/±30%）"""
    prof = get_customer_profile(conn, customer_id)
    if "error" in prof:
        return prof
    target = prof["optimize_target"]
    target_cost_metric = "open_cost" if target == "open" else "lead_cost"
    target_cost = prof["target_cost"]

    comp = get_period_comparison(conn, customer_id, cur_start, cur_end, cmp_start, cmp_end)
    chg, mc, mp, tc, tp = comp["metrics_change"], comp["metrics_cur"], comp["metrics_prev"], comp["cur"], comp["prev"]
    days_ok = check_data_completeness(conn, customer_id, cur_start, cur_end)["sufficient"]
    events = []

    # 消耗事件
    sc = pct_change(tc["spend"], tp["spend"])
    if sc is not None and abs(sc) >= spend_threshold:
        events.append({"location": "客户整体/消耗", "metric": "spend",
                       "cur": tc["spend"], "prev": tp["spend"], "change": sc,
                       "direction": "negative" if sc < 0 else "positive",
                       "is_adverse": sc < 0, "form": "单指标"})

    # 9 指标事件（客户级）
    for m in METRIC_LIST:
        c = chg.get(m)
        if c is None or abs(c) < metric_threshold:
            continue
        cur_v, prev_v = mc.get(m), mp.get(m)
        vol = min(1.0, (tc["open_msg"] + tc["lead_cnt"]) / 30) if (tc["open_msg"] + tc["lead_cnt"]) else 0
        events.append({"location": f"客户整体/{m}", "metric": m,
                       "cur": cur_v, "prev": prev_v, "change": c,
                       "direction": "positive" if c > 0 else "negative",
                       "is_adverse": (c * ADVERSE.get(m, 1)) > 0, "form": _form(m, events)})

    # 版位级 CPM / 消耗结构（pp）
    plc = get_period_comparison(conn, customer_id, cur_start, cur_end, cmp_start, cmp_end, dim="placement")
    for row in plc["rows"]:
        m = row["metrics_change"].get("CPM")
        if m is not None and abs(m) >= metric_threshold:
            events.append({"location": f"版位 {row['dim']}/CPM", "metric": "CPM",
                           "cur": row["metrics_cur"].get("CPM"), "prev": row["metrics_prev"].get("CPM"),
                           "change": m, "direction": "positive" if m > 0 else "negative",
                           "is_adverse": m > 0, "form": "结构变化"})

    # 权重 40/30/20/10
    for e in events:
        spend_abs = abs(tc["spend"] - tp["spend"])
        w_spend = min(1.0, spend_abs / max(tc["spend"], tp["spend"], 1))
        if e["metric"] in ("open_cost", "lead_cost", "spend", "CPM"):
            tv = mc.get(target_cost_metric) or 0
            w_cost = min(1.0, abs((tv or 0) - target_cost) / target_cost) if target_cost else 0
        else:
            tv = mc.get(target_cost_metric) or 0
            w_cost = min(1.0, abs((tv or 0) - target_cost) / target_cost) * 0.5 if target_cost else 0
        w_mag = min(1.0, abs(e["change"]) / 0.5)
        w_conf = (0.5 if days_ok else 0.2) + 0.5 * min(1.0, (tc["open_msg"] + tc["lead_cnt"]) / 30)
        e["weight_breakdown"] = {"spend_impact": round(w_spend, 3), "cost_contribution": round(w_cost, 3),
                                 "magnitude": round(w_mag, 3), "confidence": round(w_conf, 3)}
        e["weight"] = round(0.4 * w_spend + 0.3 * w_cost + 0.2 * w_mag + 0.1 * w_conf, 4)
        e["insufficient_data"] = not days_ok

    events.sort(key=lambda x: -x["weight"])
    for i, e in enumerate(events, 1):
        e["rank"] = i
        e["is_top3"] = i <= 3
    return {"customer_id": customer_id, "target_cost_metric": target_cost_metric,
            "events": events, "n_events": len(events)}


def _form(metric, existing):
    """异常形式（可选提示）：与已有事件构成链路组合/指标冲突"""
    prior = {e["metric"] for e in existing}
    chain = {"open_rate": {"button_rate"}, "lead_rate": {"open_rate"}, "lead_cvr": {"CTR", "button_rate"}}
    if metric in chain and chain[metric] & prior:
        return "链路组合"
    up = {m for m in prior if m in ("CTR", "button_rate", "open_rate", "lead_rate", "lead_cvr")}
    if metric in ("CTR", "button_rate", "open_rate", "lead_rate", "lead_cvr") and up:
        return "指标冲突"
    return "单指标"


# ---------------------------------------------------------------- C 类
def split_by_dimension(conn, customer_id, cur_start, cur_end, cmp_start, cmp_end, metric, dim):
    """客户+区间+指标+维度(placement/plan/note) → 各对象对总变化的贡献拆分（正负向共用）"""
    col = {"placement": "placement", "plan": "plan_id", "note": "note_id"}[dim]
    cur = {r["dim"]: r for r in _agg(conn, customer_id, cur_start, cur_end, col)}
    prev = {r["dim"]: r for r in _agg(conn, customer_id, cmp_start, cmp_end, col)}
    field = {"spend": "spend"}.get(metric, "spend")  # 贡献拆分以消耗变化为通约单位；率类指标由 LLM 结合漏斗解释
    total_delta = sum(c[field] for c in cur.values()) - sum(p[field] for p in prev.values())
    items = []
    for k in sorted(set(cur) | set(prev)):
        c, p = cur.get(k, {field: 0}), prev.get(k, {field: 0})
        d = c[field] - p[field]
        name = k
        if col == "plan_id":
            r = conn.execute("SELECT name FROM plan WHERE id=?", (k,)).fetchone()
            name = r["name"] if r else k
        if col == "note_id":
            r = conn.execute("SELECT title FROM note WHERE id=?", (k,)).fetchone()
            name = r["title"] if r else k
        items.append({"object": k, "name": name, "dim": dim,
                      "cur": c[field], "prev": p[field], "delta": d,
                      "contribution": round(d / total_delta, 4) if total_delta else None,
                      "cur_metrics": derive_metrics(c) if cur.get(k) else None,
                      "prev_metrics": derive_metrics(prev[k]) if k in prev else None})
    items.sort(key=lambda x: -(abs(x["delta"])))
    top = max(items, key=lambda x: abs(x["delta"])) if items else None
    return {"customer_id": customer_id, "metric": field, "dim": dim,
            "total_delta": total_delta, "items": items,
            "max_contributor": {"name": top["name"], "delta": top["delta"],
                                "contribution": top["contribution"]} if top else None}


def compare_new_old(conn, customer_id, start, end, obj_type="note", new_window_days=14):
    """客户+区间+对象(plan/note) → 新旧批次对比（新基建质量判断）"""
    tbl, idcol = ("plan", "plan_id") if obj_type == "plan" else ("note", "note_id")
    edge = (_d(end) - timedelta(days=new_window_days)).isoformat()
    rows = conn.execute(
        f"""SELECT m.{idcol} AS oid, o.created_date, m.spend, m.impressions, m.note_clicks,
                   m.button_clicks, m.open_msg, m.lead_cnt
            FROM daily_metric m JOIN {tbl} o ON o.id=m.{idcol}
            WHERE m.customer_id=? AND m.date>=? AND m.date<=?""", (customer_id, start, end)).fetchall()
    agg = {}
    for r in rows:
        key = r["oid"]
        a = agg.setdefault(key, {"oid": key, "created": r["created_date"],
                                 **{k: 0 for k in ["spend", "impressions", "note_clicks", "button_clicks", "open_msg", "lead_cnt"]}})
        for k in ["spend", "impressions", "note_clicks", "button_clicks", "open_msg", "lead_cnt"]:
            a[k] += r[k]
    new = {k: v for k, v in agg.items() if v["created"] > edge}
    old = {k: v for k, v in agg.items() if v["created"] <= edge}

    def summarize(group):
        if not group:
            return {"n": 0, "metrics": None}
        t = {k: sum(v[k] for v in group.values()) for k in
             ["spend", "impressions", "note_clicks", "button_clicks", "open_msg", "lead_cnt"]}
        return {"n": len(group), "spend": t["spend"], "metrics": derive_metrics(t)}
    sn, so = summarize(new), summarize(old)
    return {"customer_id": customer_id, "obj_type": obj_type, "new_window_days": new_window_days,
            "new": sn, "old": so,
            "verdict": "new_underperform" if (sn["metrics"] and so["metrics"]) and (
                (so["metrics"].get("lead_cost") or so["metrics"].get("open_cost") or 1e9) <
                (sn["metrics"].get("lead_cost") or sn["metrics"].get("open_cost") or 1e9)) else "checked"}


def drill_down_object(conn, obj_type, obj_id, start, end):
    """对象类型(plan/note)+ID+区间 → 对象级日明细 + 漏斗 + 指标"""
    idcol = "plan_id" if obj_type == "plan" else "note_id"
    rows = conn.execute(
        f"""SELECT date, placement, spend, impressions, note_clicks, button_clicks, open_msg, lead_cnt
            FROM daily_metric WHERE {idcol}=? AND date>=? AND date<=? ORDER BY date""",
        (obj_id, start, end)).fetchall()
    if not rows:
        return {"error": f"{obj_type} {obj_id} 在该区间无数据"}
    daily = []
    t = {k: 0 for k in ["spend", "impressions", "note_clicks", "button_clicks", "open_msg", "lead_cnt"]}
    for r in rows:
        daily.append(dict(date=r["date"], placement=r["placement"], spend=r["spend"],
                          impressions=r["impressions"], note_clicks=r["note_clicks"],
                          button_clicks=r["button_clicks"], open_msg=r["open_msg"], lead_cnt=r["lead_cnt"]))
        for k in t:
            t[k] += r[k]
    return {"obj_type": obj_type, "obj_id": obj_id, "start": start, "end": end,
            "daily": daily, "totals": t, "metrics": derive_metrics(t)}


# ---------------------------------------------------------------- D 类
def search_cases(conn, industry=None, sector=None, category=None, signature_terms=None, limit=5):
    """行业/赛道/品类/异常签名 → 可引用案例（referenceable=1 且非 badcase；差异判断由 LLM 做）"""
    sql = """SELECT dc.*, i.name industry, s.name sector, k.name category
             FROM diag_case dc
             LEFT JOIN industry i ON i.id=dc.industry_id
             LEFT JOIN sector s ON s.id=dc.sector_id
             LEFT JOIN category k ON k.id=dc.category_id
             WHERE dc.referenceable=1 AND dc.status!='badcase'"""
    args = []
    if industry:
        sql += " AND i.name LIKE ?"
        args.append(f"%{industry}%")
    if sector:
        sql += " AND s.name LIKE ?"
        args.append(f"%{sector}%")
    if signature_terms:
        like = " OR ".join("dc.anomaly_signature LIKE ?" for _ in signature_terms)
        sql += f" AND ({like})"
        args += [f"%{t}%" for t in signature_terms]
    sql += " ORDER BY dc.created_at DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    note = (f"召回 {len(rows)} 个可引用案例（已过滤 badcase）" if rows
            else "案例库当前为空属正常（案例由审核通过的报告沉淀）")
    return {"cases": [dict(r) for r in rows], "n": len(rows), "note": note}


def verify_evidence(claims, fact_base):
    """结论+引用数据 → 事实/推断/建议分类 + 是否缺证据
    claims: [{text, kind: fact|inference|suggestion, evidence: [引用字符串]}]
    fact_base: 工具输出序列化后的文本列表"""
    fb = " ".join(fact_base)
    import re

    def norm(s):
        return re.sub(r"[\s,，%％]", "", str(s))
    results = []
    for c in claims:
        kind = c.get("kind", "fact")
        ev_ok, missing = True, []
        if kind == "fact":
            for ev in c.get("evidence", []):
                if norm(ev) not in norm(fb):
                    ev_ok = False
                    missing.append(ev)
        results.append({"claim": c["text"], "kind": kind,
                        "verdict": "pass" if ev_ok else "missing_evidence",
                        "missing": missing})
    passed = all(r["verdict"] == "pass" or r["kind"] != "fact" for r in results)
    return {"claims": results, "all_facts_verified": passed,
            "action": "ok" if passed else "回退修改或降级为待验证假设"}


def assemble_report(context):
    """全部诊断结果 → 八章节 report_json（确定性组装；LLM 生成的文本以槽位注入）"""
    prof = context["profile"]
    comp = context["comparison"]
    anomalies = context["anomalies"]
    top3 = [e for e in anomalies["events"] if e["is_top3"]]
    watch = [e for e in anomalies["events"] if not e["is_top3"]]
    # 版位级成本超目标但整体未达异常门槛 → 进观察清单（judge 校准发现 v1 漏记此类项）
    prof_target = prof.get("target_cost")
    if prof_target:
        tcm = "open_cost" if prof.get("optimize_target") == "open" else "lead_cost"
        splits = (context.get("drill") or {}).get("splits") or {}
        for it in (splits.get("placement") or {}).get("items", []):
            v = (it.get("cur_metrics") or {}).get(tcm)
            if v and v > prof_target and not any(str(it.get("name")) in str(w.get("location", "")) for w in watch):
                watch.append({"location": f"版位 {it.get('name')}/{tcm}",
                              "change": f"{v:.2f} 元，超目标 {prof_target} 元，整体未达异常门槛，持续观察"})
    adverse_top3 = [e for e in top3 if e.get("is_adverse")]
    degraded = bool((context.get("data_check") or {}).get("degraded"))
    if degraded:
        # 完整性拦截分支（交接文档 5.1/9.3 节点1）：拒绝归因，整体状态不能是"正常"
        overall = "需行动"
    elif not anomalies["events"]:
        overall = "正常"
    elif not adverse_top3:
        overall = "需关注"
    else:
        overall = "需行动"
    data_check = context.get("data_check", {})
    chapters = {
        "1_封面": {"customer": prof["name"], "industry": prof["industry"], "sector": prof["sector"],
                   "categories": prof["categories"], "period": f"{context['cur_start']}~{context['cur_end']} vs {context['cmp_start']}~{context['cmp_end']}",
                   "version": context.get("report_version", 1),
                   "generated_at": context.get("generated_at") or datetime.now().isoformat(timespec="seconds")},
        "2_核心结论": {"overall_status": overall,
                       "data_status": "数据不足" if degraded else "完整",
                       "data_check": {"cur_missing": data_check.get("cur", {}).get("missing_days", []),
                                      "prev_missing": data_check.get("prev", {}).get("missing_days", [])},
                       "summary": context.get("llm_summary", "（LLM 摘要待生成）"),
                       "top3": [{"location": e["location"], "change": e["change"], "weight": e["weight"],
                                 "direction": e["direction"]} for e in top3]},
        "3_指标与趋势": {"metrics_cur": comp["metrics_cur"], "metrics_prev": comp["metrics_prev"],
                         "metrics_change": comp["metrics_change"],
                         "funnel": context.get("funnel"), "trend_14d": context.get("trend"),
                         "trend_spend": context.get("trend_spend"),
                         "trend_metric": context.get("trend_metric")},
        "4_分层诊断": context.get("layer_diagnosis", []),
        "5_异常与原因": {"top3_detail": context.get("llm_top3_detail", "（待 LLM 归因）"),
                         "watchlist": [{"location": e["location"], "change": e["change"]} for e in watch]},
        "6_案例参考": ({"refs": context["case_refs"], "n": len(context["case_refs"])}
                       if context.get("case_refs")
                       else context.get("cases", {"cases": [], "n": 0})),
        "7_优化建议": context.get("llm_suggestions", []),
        "8_行动计划": context.get("llm_action_plan", []),
    }
    return {"schema_version": "report-v1", "overall_status": overall, "chapters": chapters}


# ---------------------------------------------------------------- 注册表
TOOL_REGISTRY = {
    "get_customer_profile": get_customer_profile,
    "get_daily_metrics": get_daily_metrics,
    "get_period_comparison": get_period_comparison,
    "get_funnel": get_funnel,
    "get_trend": get_trend,
    "get_infrastructure": get_infrastructure,
    "check_data_completeness": check_data_completeness,
    "detect_anomalies": detect_anomalies,
    "split_by_dimension": split_by_dimension,
    "compare_new_old": compare_new_old,
    "drill_down_object": drill_down_object,
    "search_cases": search_cases,
    "verify_evidence": verify_evidence,
    "assemble_report": assemble_report,
}


def call_tool(conn, name, params):
    """统一入口：参数 JSON → 结果 JSON（tracing 由 orchestrator 包裹）"""
    fn = TOOL_REGISTRY.get(name)
    if not fn:
        return {"error": f"unknown tool: {name}"}
    try:
        if name == "verify_evidence":
            return fn(**params)  # 不依赖 conn
        elif name == "assemble_report":
            return fn(params)
        else:
            return fn(conn, **params)
    except Exception as ex:
        return {"error": f"{type(ex).__name__}: {ex}"}
