#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断台 · 零依赖后端 HTTP 服务（Phase 3 前端承载）

技术选型：标准库 http.server，零三方依赖（与 scheduler.py 同风格）。
职责：
  - 托管 web/ 下的前端静态文件
  - 提供 JSON API，背靠现有 SQLite + tools.py / orchestrator.py / ingest.py

API：
  GET  /api/customers          客户列表（支持 industry/status/source/q 筛选；附本周 KPI + 环比）
  GET  /api/weeks?customer_id= 该客户可用的自然周（周一）列表
  GET  /api/report?customer_id=&week=&metric_threshold=&spend_threshold=&dry_run=
                               生成某客户某周诊断报告（默认 dry_run，离线确定性，秒级）
  GET  /api/compare?customer_id=&weeks=w1,w2,w3   多周指标对比
  GET  /api/cases?industry=&sector=&signature=    案例检索（现有 SQL 匹配 RAG）
  POST /api/ingest             手动录入客户（接 ingest.ingest_customer）

用法：
  python3 api_server.py                 # 默认 http://127.0.0.1:8000
  python3 api_server.py --port 9000
  python3 api_server.py --db data/ad_review.db
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR = os.path.join(HERE, "agent")
DATA_DIR = os.path.join(HERE, "data")
WEB_DIR = os.path.join(HERE, "web")
sys.path.insert(0, AGENT_DIR)
sys.path.insert(0, DATA_DIR)

import tools
import orchestrator
import ingest as ingest_mod

DEFAULT_DB = os.path.join(HERE, "data", "ad_review.db")


# ---------------------------------------------------------------- 工具
def get_conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _d(s):
    return date.fromisoformat(s)


def week_start_of(d):
    """返回 d 所在自然周的周一"""
    return d - timedelta(days=d.weekday())


def available_weeks(conn, customer_id):
    """该客户有数据的自然周（周一）列表，倒序"""
    rows = conn.execute(
        "SELECT DISTINCT date FROM daily_metric WHERE customer_id=?", (customer_id,)).fetchall()
    if not rows:
        return []
    weeks = set()
    for r in rows:
        weeks.add(week_start_of(_d(r["date"])))
    return sorted(weeks, reverse=True)


def customer_kpi(conn, customer_id):
    """本周 vs 上周 的核心 KPI（用于列表/总览卡片）"""
    cs, ce, ps, pe = orchestrator.derive_periods(conn, customer_id)
    comp = tools.get_period_comparison(conn, customer_id, cs, ce, ps, pe)
    mc, mp, chg = comp["metrics_cur"], comp["metrics_prev"], comp["metrics_change"]
    cur_spend = (comp.get("cur") or {}).get("spend")
    prev_spend = (comp.get("prev") or {}).get("spend")
    spend_change = tools.pct_change(cur_spend, prev_spend) if (cur_spend is not None and prev_spend is not None) else None
    target_cost_metric = "open_cost" if (tools.get_customer_profile(conn, customer_id).get("optimize_target") == "open") else "lead_cost"
    return {
        "cur_start": cs, "cur_end": ce, "cmp_start": ps, "cmp_end": pe,
        "spend": cur_spend, "spend_change": spend_change,
        "lead_cnt": mc.get("lead_cnt"), "open_msg": mc.get("open_msg"),
        "lead_cost": mc.get(target_cost_metric), "lead_cost_change": chg.get(target_cost_metric),
        "open_cost": mc.get("open_cost"), "open_cost_change": chg.get("open_cost"),
        "ctr": mc.get("CTR"), "ctr_change": chg.get("CTR"),
    }


# ---------------------------------------------------------------- 路由实现
def api_customers(db_path, params):
    conn = get_conn(db_path)
    industry = (params.get("industry", [""])[0] or "").strip()
    status = (params.get("status", [""])[0] or "").strip()
    source = (params.get("source", [""])[0] or "").strip()
    q = (params.get("q", [""])[0] or "").strip()
    sql = """SELECT c.id, c.name, c.optimize_target, c.target_cost, c.status, c.source,
                    i.name AS industry, s.name AS sector
             FROM customer c
             JOIN sector s ON s.id=c.sector_id
             JOIN industry i ON i.id=s.industry_id
             WHERE 1=1"""
    args = []
    if industry:
        sql += " AND i.name LIKE ?"; args.append(f"%{industry}%")
    if status:
        sql += " AND c.status = ?"; args.append(status)
    if source:
        sql += " AND c.source = ?"; args.append(source)
    if q:
        sql += " AND c.name LIKE ?"; args.append(f"%{q}%")
    sql += " ORDER BY c.id"
    out = []
    for r in conn.execute(sql, args).fetchall():
        cats = [x["name"] for x in conn.execute(
            """SELECT k.name FROM category k JOIN customer_category cc ON cc.category_id=k.id
               WHERE cc.customer_id=?""", (r["id"],))]
        item = {k: r[k] for k in ("id", "name", "optimize_target", "target_cost",
                                  "status", "source", "industry", "sector")}
        item["categories"] = cats
        try:
            item["kpi"] = customer_kpi(conn, r["id"])
        except Exception:
            item["kpi"] = None
        out.append(item)
    conn.close()
    return out


def api_weeks(db_path, params):
    cid = int(params.get("customer_id", ["0"])[0])
    conn = get_conn(db_path)
    weeks = available_weeks(conn, cid)
    conn.close()
    return [w.isoformat() for w in weeks]


def api_report(db_path, params):
    cid = int(params.get("customer_id", ["0"])[0])
    week = (params.get("week", [""])[0] or "").strip()
    mt = params.get("metric_threshold", [""])[0]
    st = params.get("spend_threshold", [""])[0]
    dry_run = (params.get("dry_run", ["true"])[0]).lower() != "false"
    mt = float(mt) if mt else None
    st = float(st) if st else None
    conn = get_conn(db_path)
    if week:
        ws = _d(week)
        cs, ce = ws, ws + timedelta(days=6)
        ps, pe = ws - timedelta(days=7), ws - timedelta(days=1)
    else:
        cs, ce, ps, pe = orchestrator.derive_periods(conn, cid)
    o = orchestrator.ReviewOrchestrator(db_path)
    try:
        report = o.run(customer_id=cid, dry_run=dry_run,
                       metric_threshold=mt, spend_threshold=st,
                       cur_start=cs, cur_end=ce, cmp_start=ps, cmp_end=pe)
    finally:
        o.close()
    conn.close()
    if "error" in report:
        return {"error": report["error"]}
    report["params"] = {"week": cs, "metric_threshold": mt, "spend_threshold": st, "dry_run": dry_run}
    return report


def api_compare(db_path, params):
    cid = int(params.get("customer_id", ["0"])[0])
    weeks = [w for w in (params.get("weeks", [""])[0] or "").split(",") if w]
    conn = get_conn(db_path)
    prof = tools.get_customer_profile(conn, cid)
    target_cost_metric = "open_cost" if prof.get("optimize_target") == "open" else "lead_cost"
    out = []
    for w in weeks:
        try:
            ws = _d(w)
        except Exception:
            continue
        cs, ce = ws, ws + timedelta(days=6)
        ps, pe = ws - timedelta(days=7), ws - timedelta(days=1)
        comp = tools.get_period_comparison(conn, cid, cs, ce, ps, pe)
        mc, chg = comp["metrics_cur"], comp["metrics_change"]
        out.append({
            "week": w, "week_label": f"{cs}~{ce}",
            "spend": (comp.get("cur") or {}).get("spend"),
            "lead_cnt": mc.get("lead_cnt"), "open_msg": mc.get("open_msg"),
            "lead_cost": mc.get(target_cost_metric), "open_cost": mc.get("open_cost"),
            "ctr": mc.get("CTR"), "cpm": mc.get("CPM"), "cpc": mc.get("CPC"),
            "spend_change": comp.get("spend_change"),
            "lead_cost_change": chg.get(target_cost_metric),
            "open_cost_change": chg.get("open_cost"),
            "ctr_change": chg.get("CTR"),
        })
    conn.close()
    return {"customer": prof.get("name"), "rows": out}


def api_cases(db_path, params):
    industry = (params.get("industry", [""])[0] or "").strip() or None
    sector = (params.get("sector", [""])[0] or "").strip() or None
    signature = (params.get("signature", [""])[0] or "").strip()
    sig_terms = [t.strip() for t in signature.split(",") if t.strip()] or None
    conn = get_conn(db_path)
    res = tools.search_cases(conn, industry=industry, sector=sector, signature_terms=sig_terms)
    conn.close()
    return res


def api_ingest(db_path, body):
    try:
        payload = json.loads(body)
    except Exception as e:
        return {"error": f"JSON 解析失败: {e}"}
    conn = get_conn(db_path)
    try:
        res = ingest_mod.ingest_customer(conn, payload)
    except Exception as e:
        conn.close()
        return {"error": f"录入失败: {type(e).__name__}: {e}"}
    conn.close()
    return res


# ---------------------------------------------------------------- HTTP 处理
class Handler(BaseHTTPRequestHandler):
    db_path = DEFAULT_DB

    def _send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path):
        if not os.path.isfile(path):
            self._send_json({"error": "not found"}, 404)
            return
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        ctype = {"html": "text/html; charset=utf-8", "js": "application/javascript; charset=utf-8",
                 "css": "text/css; charset=utf-8", "json": "application/json; charset=utf-8",
                 "svg": "image/svg+xml", "ico": "image/x-icon"}.get(ext, "application/octet-stream")
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        if path.startswith("/api/"):
            try:
                if path == "/api/customers":
                    self._send_json(api_customers(self.db_path, params))
                elif path == "/api/weeks":
                    self._send_json(api_weeks(self.db_path, params))
                elif path == "/api/report":
                    self._send_json(api_report(self.db_path, params))
                elif path == "/api/compare":
                    self._send_json(api_compare(self.db_path, params))
                elif path == "/api/cases":
                    self._send_json(api_cases(self.db_path, params))
                else:
                    self._send_json({"error": f"unknown api: {path}"}, 404)
            except Exception as e:
                self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)
            return
        # 静态文件
        rel = path.lstrip("/")
        if rel == "" or rel == "/":
            rel = "index.html"
        fpath = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not fpath.startswith(WEB_DIR):
            self._send_json({"error": "forbidden"}, 403)
            return
        self._send_file(fpath)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        if path == "/api/ingest":
            try:
                self._send_json(api_ingest(self.db_path, body.decode("utf-8")))
            except Exception as e:
                self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)
        else:
            self._send_json({"error": f"unknown api: {path}"}, 404)

    def log_message(self, fmt, *args):
        sys.stderr.write("[api] " + (fmt % args) + "\n")


def main():
    ap = argparse.ArgumentParser(description="诊断台 前端后端服务")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()
    Handler.db_path = args.db
    if not os.path.exists(args.db):
        sys.stderr.write(f"[warn] DB 不存在: {args.db}\n")
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    sys.stderr.write(f"诊断台前端已启动: http://{args.host}:{args.port}\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
