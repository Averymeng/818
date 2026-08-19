#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断台 · 模拟数据定时刷新（Phase 2 自动端）

只重建 source='sim' 的模拟数据，完整保留 source='upload' 的用户上传数据。
每次刷新用「日期派生种子」使数值变化，演示「数据在更新」；sim_version 同步更新。

典型调用（交给 scheduler.py 常驻循环，或 crontab）：
  python3 data/refresh_sim.py --run-once [--db /path/ad_review.db]
"""
import os
import sqlite3
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate import build_sim, SEED

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ad_review.db")


def ensure_customer_source(conn):
    """老库兼容：补 source 列并把历史行标记为 sim（幂等）。"""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(customer)")]
    if "source" not in cols:
        conn.execute(
            "ALTER TABLE customer ADD COLUMN source TEXT NOT NULL DEFAULT 'sim' "
            "CHECK (source IN ('sim','upload'))")
    conn.execute("UPDATE customer SET source='sim' WHERE source IS NULL OR source=''")


def refresh_sim(db_path=DB, sim_version=None):
    """删除全部 sim 数据后重建；upload 数据（customer/plan/note/daily_metric）原样保留。"""
    conn = sqlite3.connect(db_path)
    ensure_customer_source(conn)
    today = date.today().isoformat()
    ver = sim_version or f"sim-{today}"
    seed = SEED + int(today.replace("-", ""))  # 日期派生种子 → 数值变化，演示「数据在更新」

    sim_ids = [r[0] for r in conn.execute("SELECT id FROM customer WHERE source='sim'")]
    if sim_ids:
        ph = ",".join("?" * len(sim_ids))
        conn.execute("DELETE FROM daily_metric WHERE source='sim'")
        conn.execute(f"DELETE FROM plan WHERE customer_id IN ({ph})", sim_ids)
        conn.execute(f"DELETE FROM note WHERE customer_id IN ({ph})", sim_ids)
        conn.execute("DELETE FROM customer WHERE source='sim'")

    build_sim(conn, ver, seed)
    conn.commit()
    conn.close()
    return {"sim_version": ver, "rebuilt_sim_customers": len(sim_ids)}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="诊断台 模拟数据刷新（保留上传）")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--run-once", action="store_true", help="立即刷新一次并退出")
    args = ap.parse_args()
    if args.run_once:
        print("refresh ->", refresh_sim(args.db))
    else:
        print("请加 --run-once 立即刷新，或用 scheduler.py 启动常驻循环")
