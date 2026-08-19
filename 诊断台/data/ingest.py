#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断台 · 用户手动录入后端（Phase 2）

客户/用户在前端小入口或直接编辑与前端绑定的本地表时，后端走本模块把数据写入现有表。
约束（用户明确）：
  - 字段不变：沿用 db/schema.sql 现有列，只增数据行，不动态建表。
  - daily_metric 写入时 source='upload'（schema 已有该列，区分 sim/upload）；其余表按现有结构写入。
  - 报告生成不区分数据来源（谁灌进库就按谁算）。

payload 结构（JSON，见 data/sample_customer_upload.json）：
{
  "customer": {"name","industry","sector","categories":[...],"optimize_target","target_cost"},
  "plans":    [{"key","name","category","placement","created_date","status","daily_budget","stopped_date"(可选)}],
  "notes":    [{"key","plan_key","category","title","material_form","created_date","status","stopped_date"(可选)}],
  "daily_metrics": [{"plan_key","note_key","category","placement","date",
                     "spend","impressions","note_clicks","button_clicks","open_msg","lead_cnt"}]
}

用法:
  python3 data/ingest.py --json path/to/customer.json          # 整客户录入
  python3 data/ingest.py --json x.json --db /path/to/ad_review.db
  python3 data/ingest.py --sample                              # 导出示例 JSON 到 stdout
  python3 data/ingest.py --csv-daily daily.csv --customer-id 51 --db ...   # 追加日明细(需先有 plan/note)
"""
import argparse
import csv
import json
import os
import sqlite3
import sys

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ad_review.db")


def _get_or_create(conn, table, name_col, name, extra_cols=None, extra_vals=None):
    """按 name(+额外唯一键) 取 id，没有就插入并返回新 id。"""
    where = f"{name_col}=?"
    vals = [name]
    if extra_cols:
        for c, v in zip(extra_cols, extra_vals):
            where += f" AND {c}=?"
            vals.append(v)
    row = conn.execute(f"SELECT id FROM {table} WHERE {where}", vals).fetchone()
    if row:
        return row[0]
    cols = [name_col] + (extra_cols or [])
    ins = [name] + (extra_vals or [])
    cur = conn.execute(
        f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", ins)
    return cur.lastrowid


def ingest_customer(conn, payload):
    """写入一个客户的 plan/note/daily_metric。返回统计 dict。"""
    c = payload["customer"]
    industry_id = _get_or_create(conn, "industry", "name", c["industry"])
    sector_id = _get_or_create(conn, "sector", "name", c["sector"], ["industry_id"], [industry_id])

    exist = conn.execute("SELECT id FROM customer WHERE name=?", (c["name"],)).fetchone()
    if exist:
        raise ValueError(f"客户「{c['name']}」已存在(customer_id={exist[0]})，请换名或先清理")
    cid = conn.execute(
        "INSERT INTO customer(name, sector_id, optimize_target, target_cost, status) VALUES(?,?,?,?,?)",
        (c["name"], sector_id, c["optimize_target"], c["target_cost"], "active")).lastrowid
    for cat in c.get("categories", []):
        cat_id = _get_or_create(conn, "category", "name", cat)
        conn.execute("INSERT INTO customer_category VALUES(?,?)", (cid, cat_id))

    plan_ids, note_ids = {}, {}
    for i, p in enumerate(payload.get("plans", [])):
        cat_id = _get_or_create(conn, "category", "name", p["category"])
        pid = conn.execute(
            "INSERT INTO plan(customer_id, category_id, name, placement, created_date, status, daily_budget, stopped_date) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (cid, cat_id, p["name"], p["placement"], p["created_date"], p["status"],
             p["daily_budget"], p.get("stopped_date"))).lastrowid
        plan_ids[p.get("key", i)] = pid

    for i, n in enumerate(payload.get("notes", [])):
        pid = plan_ids[n["plan_key"]]
        cat_id = _get_or_create(conn, "category", "name", n["category"])
        nid = conn.execute(
            "INSERT INTO note(customer_id, category_id, plan_id, title, material_form, created_date, status, stopped_date) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (cid, cat_id, pid, n["title"], n["material_form"], n["created_date"], n["status"],
             n.get("stopped_date"))).lastrowid
        note_ids[n.get("key", i)] = nid

    n_rows = 0
    for d in payload.get("daily_metrics", []):
        pid = plan_ids[d["plan_key"]]
        nid = note_ids[d["note_key"]]
        cat_id = _get_or_create(conn, "category", "name", d["category"])
        conn.execute(
            "INSERT OR REPLACE INTO daily_metric"
            "(date, customer_id, category_id, placement, plan_id, note_id, spend, impressions, note_clicks, "
            " button_clicks, open_msg, lead_cnt, source, sim_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d["date"], cid, cat_id, d["placement"], pid, nid, d["spend"], d["impressions"],
             d["note_clicks"], d["button_clicks"], d["open_msg"], d["lead_cnt"], "upload", "upload"))
        n_rows += 1

    conn.commit()
    return {"customer_id": cid, "plans": len(plan_ids), "notes": len(note_ids), "daily_rows": n_rows}


def ingest_daily_csv(conn, csv_path, customer_id):
    """CSV 追加日明细到已存在 customer（需先有 plan/note）。
    列: date,plan_id,note_id,category,placement,spend,impressions,note_clicks,button_clicks,open_msg,lead_cnt
    """
    n = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            conn.execute(
                "INSERT OR REPLACE INTO daily_metric"
                "(date, customer_id, category_id, placement, plan_id, note_id, spend, impressions, note_clicks, "
                " button_clicks, open_msg, lead_cnt, source, sim_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["date"], customer_id, int(r["category_id"]), r["placement"], int(r["plan_id"]),
                 int(r["note_id"]), float(r["spend"]), int(r["impressions"]), int(r["note_clicks"]),
                 int(r["button_clicks"]), int(r["open_msg"]), int(r["lead_cnt"]), "upload", "upload"))
            n += 1
    conn.commit()
    return n


def _sample():
    return {
        "customer": {
            "name": "示例上传客户_美妆个护",
            "industry": "到综服务", "sector": "美妆个护",
            "categories": ["护肤"], "optimize_target": "lead", "target_cost": 80
        },
        "plans": [
            {"key": 0, "name": "信息流_护肤主推", "category": "护肤", "placement": "feed",
             "created_date": "2026-08-01", "status": "在投", "daily_budget": 500},
            {"key": 1, "name": "搜索_护肤词包", "category": "护肤", "placement": "search",
             "created_date": "2026-08-05", "status": "在投", "daily_budget": 300}
        ],
        "notes": [
            {"key": 0, "plan_key": 0, "category": "护肤", "title": "夏日护肤图文A", "material_form": "图文",
             "created_date": "2026-08-01", "status": "在投"},
            {"key": 1, "plan_key": 1, "category": "护肤", "title": "护肤搜索视频B", "material_form": "视频",
             "created_date": "2026-08-05", "status": "在投"}
        ],
        "daily_metrics": [
            {"plan_key": 0, "note_key": 0, "category": "护肤", "placement": "feed", "date": "2026-08-11",
             "spend": 480, "impressions": 12000, "note_clicks": 360, "button_clicks": 90, "open_msg": 12, "lead_cnt": 6},
            {"plan_key": 1, "note_key": 1, "category": "护肤", "placement": "search", "date": "2026-08-11",
             "spend": 290, "impressions": 8000, "note_clicks": 240, "button_clicks": 70, "open_msg": 9, "lead_cnt": 5}
        ]
    }


def main():
    ap = argparse.ArgumentParser(description="诊断台 用户手动录入后端")
    ap.add_argument("--json", help="客户录入 JSON 路径")
    ap.add_argument("--csv-daily", help="日明细 CSV 路径（需 --customer-id）")
    ap.add_argument("--customer-id", type=int, help="CSV 追加时的 customer_id")
    ap.add_argument("--db", default=DB, help="目标 sqlite 路径（默认 data/ad_review.db）")
    ap.add_argument("--sample", action="store_true", help="打印示例 JSON 并退出")
    args = ap.parse_args()

    if args.sample:
        print(json.dumps(_sample(), ensure_ascii=False, indent=2))
        return

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.json:
            payload = json.load(open(args.json, encoding="utf-8"))
            res = ingest_customer(conn, payload)
            print("录入成功:", json.dumps(res, ensure_ascii=False))
        elif args.csv_daily:
            if not args.customer_id:
                sys.exit("--csv-daily 需要 --customer-id")
            n = ingest_daily_csv(conn, args.csv_daily, args.customer_id)
            print(f"日明细追加成功: {n} 行 (customer_id={args.customer_id}, source=upload)")
        else:
            sys.exit("需要提供 --json 或 --csv-daily（或 --sample 查看示例）")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
