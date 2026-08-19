#!/usr/bin/env python3
"""CI 自动回归（db/LLM-free）：针对已提交的报告 artifacts 做两道检查。

1) 已提交的 eval_results.json 必须 passed == total（全量结果一致性）。
2) 重新跑 db-free 横切扫描（E24 组，75 条规则），针对已提交报告全文扫描。

任一失败则退出码非 0，GitHub Actions 该 job 变红。
用于每次 push/PR 的「静态回归」门禁，不花 LLM 积分、不依赖数据库。
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_eval as R  # noqa: E402


def main():
    # 1) 提交态全量一致性
    res = json.loads((R.ART / "eval_results.json").read_text(encoding="utf-8"))
    assert res["passed"] == res["total"], (
        f"eval_results.json 不一致: passed={res['passed']} total={res['total']}"
    )

    # 2) 重新跑横切扫描（E24 组，db/LLM-free）
    R.run_sweep_asserts()
    fails = [(c, a["name"]) for c, asl in R.ASSERTS.items()
             for a in asl if not a["ok"]]
    assert not fails, f"横切扫描未通过: {fails}"

    print(f"CI 回归通过：committed {res['passed']}/{res['total']} + 横切扫描全过")


if __name__ == "__main__":
    main()
