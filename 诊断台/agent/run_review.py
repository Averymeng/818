#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断台 · 周度复盘 CLI 入口
用法:
  python run_review.py "悦颜美容SPA"          # 完整跑（需 DEEPSEEK_API_KEY）
  python run_review.py "悦颜美容SPA" --dry    # 跳过 LLM 节点，仅确定性链路 + 规则建议
  python run_review.py "悦颜美容SPA" --db ../data/ad_review.db
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from orchestrator import ReviewOrchestrator

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "ad_review.db"


def main():
    ap = argparse.ArgumentParser(description="诊断台 · 周度复盘")
    ap.add_argument("customer", help="客户名（如 悦颜美容SPA）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 库路径")
    ap.add_argument("--dry", action="store_true", help="dry run：跳过 LLM 节点")
    ap.add_argument("--out", help="报告 JSON 输出路径（默认仅打印摘要）")
    args = ap.parse_args()

    if not args.dry and not os.environ.get("DEEPSEEK_API_KEY"):
        print("缺少 DEEPSEEK_API_KEY（export DEEPSEEK_API_KEY=sk-...）\n用 --dry 可先跑确定性链路。")
        sys.exit(1)

    orch = ReviewOrchestrator(args.db)
    try:
        r = orch.run(customer_name=args.customer, dry_run=args.dry)
        if "error" in r:
            print(json.dumps(r, ensure_ascii=False, indent=2)); sys.exit(1)
        print(json.dumps({
            "task_id": r.get("task_id"), "overall_status": r.get("overall_status"),
            "llm_calls": r.get("llm_calls"), "llm_cost_yuan": r.get("llm_cost_yuan"),
            "top3": [e["location"] for e in r.get("chapters", {}).get("2_核心结论", {}).get("top3", [])],
        }, ensure_ascii=False, indent=2))
        if args.out:
            Path(args.out).write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n报告已写入: {args.out}")
    finally:
        orch.close()


if __name__ == "__main__":
    main()
