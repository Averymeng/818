#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断台 · badcase 库播种（幂等，可重复执行）

把评测期挖出的「智能体缺陷」固化进独立的 diag_badcase 表（与参考案例库 diag_case 物理分离）。
每条 badcase = 错误现象 + 根因 + 对应红线/代码修复 + 关联评测用例 + 状态。

用法:
  python3 诊断台/data/seed_badcase.py
前置: db/schema.sql 已应用（本脚本自带 CREATE TABLE IF NOT EXISTS，可独立运行）
"""
import json
import os
import sqlite3

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ad_review.db")

# 评测期挖出的 3 个智能体缺陷（见 eval_report_v2.md / 评测集 E24 组）
BADCASES = [
    {
        "title": "周值/日值混淆：单日值当作周值使用",
        "category": "周值日值混淆",
        "error_output": "巅峰密室报告把 14 日趋势中的单日值（如 21.45）当作 §3 周值表的周值使用，导致 Top3 异动与摘要口径错配。",
        "root_cause": "摘要/Top3/建议依据未强制限定取自 §3 周值表，LLM 误用 14 日趋势的单日明细值。",
        "red_line_fix": "system_prompt 第7节红线『周值/日值』：摘要/Top3/依据必须取自 §3 周值表，不得用单日趋势值；run_eval E24e 横切断言（正则比对 §2 摘要值与 §3 周值表）。",
        "eval_case": "E24e",
        "status": "fixed",
    },
    {
        "title": "建议依据数字不可核验 / 口径标签错配",
        "category": "依据不可核验",
        "error_output": "银龄声乐课堂 suggestion basis 引用了下钻上下文中不存在的数字（『贡献消耗下降 67.2%』），且把口径错标为『消耗占比 67.2%』（真实占比是 82.5%）。",
        "root_cause": "建议依据未强制要求数字可核验；『贡献度』与『占比』口径混用。",
        "red_line_fix": "system_prompt 第7节红线『依据数字可核验』+『口径标签』（贡献度必须写『贡献了X变化的N%』，禁写『占比N%』）；run_eval E24 口径断言。",
        "eval_case": "E24",
        "status": "fixed",
    },
    {
        "title": "观察清单漏项：版位成本超目标未进观察清单",
        "category": "观察清单漏项",
        "error_output": "码上AI学堂 feed 版位成本 25.40 元 > 目标 25 元，但整体未达异常门槛，报告未将其纳入观察清单。",
        "root_cause": "assemble_report 的观察清单逻辑未覆盖『版位级成本超目标但整体未达异常门槛』这一类项。",
        "red_line_fix": "orchestrator assemble_report 代码补观察清单逻辑：版位成本超目标且整体未达异常门槛 → 进观察清单；run_eval 复跑验证。",
        "eval_case": "assemble_report 代码修复",
        "status": "fixed",
    },
]


def main():
    con = sqlite3.connect(DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS diag_badcase (
               id INTEGER PRIMARY KEY,
               source_report_id INTEGER,
               customer_id INTEGER,
               title TEXT NOT NULL,
               category TEXT,
               error_output TEXT,
               root_cause TEXT,
               red_line_fix TEXT,
               eval_case TEXT,
               status TEXT NOT NULL DEFAULT 'fixed',
               created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))"""
    )
    con.execute("DELETE FROM diag_badcase")  # 幂等：清空后重种（手动新增的 badcase 走单独流程，不在此脚本）
    for b in BADCASES:
        con.execute(
            """INSERT INTO diag_badcase(title, category, error_output, root_cause, red_line_fix, eval_case, status)
               VALUES (?,?,?,?,?,?,?)""",
            (b["title"], b["category"], b["error_output"], b["root_cause"],
             b["red_line_fix"], b["eval_case"], b["status"]))
    con.commit()
    rows = con.execute(
        "SELECT id, category, status FROM diag_badcase ORDER BY id").fetchall()
    for r in rows:
        print(dict(zip(["id", "category", "status"], r)))
    con.close()
    print("badcase 已种入 diag_badcase，共 %d 条" % len(BADCASES))


if __name__ == "__main__":
    main()
