#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6+1 修复验证重跑：6 份受影响报告 → artifacts/（覆盖旧版），不落 eval_case（避免污染官方评测轮）

用法: python3 eval/rerun_fixed.py
日志: eval/rerun.log（终端同步输出，可 tail -f）
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from run_eval import ART, real_run, _Tee  # noqa: E402

# 6+1 修复涉及的 6 份报告（prompt 全量改动 → 全部重跑）
NAMES = ["银龄声乐课堂", "枕水人家客栈", "巅峰密室"]


def main():
    sys.stdout = _Tee(HERE / "rerun.log")
    import datetime
    print(f"\n===== 修复验证重跑 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====", flush=True)
    for n in NAMES:
        print(f"[rerun] {n} ...", flush=True)
        r, tid = real_run(n, ART / f"{n}.json")
        if "error" in r:
            print(f"  !! 失败: {r['error']}", flush=True)
        else:
            print(f"  overall={r['overall_status']} llm_calls={r['llm_calls']} "
                  f"cost=¥{r.get('llm_cost_yuan', 0):.4f} task={tid}", flush=True)
    print("重跑完成。", flush=True)


if __name__ == "__main__":
    main()
