#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断台 · 定时刷新调度（Phase 2 自动端）

每天 08:00 自动重灌模拟数据（演示「数据在更新」），完整保留用户上传数据。
零三方依赖：自带睡眠循环，常驻进程即可；也可改用系统 crontab（见底部）。

用法:
  python3 data/scheduler.py             # 常驻循环（默认每天 08:00 刷新）
  python3 data/scheduler.py --run-once  # 立即刷新一次并退出（测试用）
  python3 data/scheduler.py --at 08:30  # 改刷新时刻（默认 08:00）

生产推荐用 crontab，省去常驻进程:
  0 8 * * * cd /path/诊断台 && /path/python data/refresh_sim.py --run-once >> data/refresh.log 2>&1
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from refresh_sim import refresh_sim, DB as DEFAULT_DB


def run_loop(db_path, hour=8, minute=0):
    while True:
        now = datetime.now()
        nxt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        secs = (nxt - now).total_seconds()
        print(f"[{now:%Y-%m-%d %H:%M:%S}] 下次刷新预约 {nxt:%Y-%m-%d %H:%M:%S}（约 {secs/3600:.1f}h 后）")
        time.sleep(secs)
        try:
            r = refresh_sim(db_path)
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 已刷新模拟数据: {r}")
        except Exception as e:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 刷新失败: {e}")


def main():
    ap = argparse.ArgumentParser(description="诊断台 定时刷新模拟数据")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--run-once", action="store_true", help="立即刷新一次并退出（测试用）")
    ap.add_argument("--at", default="08:00", help="刷新时刻 HH:MM（默认 08:00）")
    args = ap.parse_args()
    h, m = (int(x) for x in args.at.split(":"))
    if args.run_once:
        print("refresh ->", refresh_sim(args.db))
        return
    run_loop(args.db, h, m)


if __name__ == "__main__":
    main()
