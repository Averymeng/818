#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E15 前置：种 2 个案例到 diag_case（幂等，可重复执行）
①可引用案例：婚纱摄影 lead_cost上涨+CTR下降→素材疲劳（referenceable=1）
②badcase：无数据断言响应慢（referenceable=0），与案例①同行业/赛道
signature 均含 lead_cost 词元，确保 search_cases 按拾光 top3 词元 OR 匹配时能召回到①、且②被过滤。
用法: python3 诊断台/eval/seed_cases.py
"""
import json
import os
import sqlite3

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ad_review.db")

CASES = [
    {
        "industry_id": 2,          # 影像婚美
        "sector_id": 4,            # 婚纱摄影
        "category_id": 21,         # 旅拍婚纱照
        "optimize_target": "lead",
        "anomaly_signature": "lead_cost上涨+CTR下降（素材疲劳，更换主图后7天成本回落）",
        "key_evidence_json": {"CTR": "-0.18", "lead_cost": "+0.22", "素材形式": "图文→视频"},
        "action_taken": "更换主图、降低素材疲劳度，保留高点击标题结构",
        "result_after": "CTR 回升 18pp，lead_cost 回落 22%，恢复目标成本",
        "status": "reference",
        "referenceable": 1,
    },
    {
        "industry_id": 2,
        "sector_id": 4,
        "category_id": 21,
        "optimize_target": "lead",
        "anomaly_signature": "lead_cost上涨（无数据断言响应慢）",
        "key_evidence_json": {"note": "该客户无私信回复/响应速度数据，严禁断言响应慢"},
        "action_taken": "-",
        "result_after": "-",
        "status": "badcase",
        "referenceable": 0,
    },
]


def main():
    con = sqlite3.connect(DB)
    con.execute("DELETE FROM diag_case")
    for c in CASES:
        con.execute(
            """INSERT INTO diag_case(industry_id, sector_id, category_id, optimize_target,
                   anomaly_signature, key_evidence_json, action_taken, result_after, status, referenceable)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (c["industry_id"], c["sector_id"], c["category_id"], c["optimize_target"],
             c["anomaly_signature"], json.dumps(c["key_evidence_json"], ensure_ascii=False),
             c["action_taken"], c["result_after"], c["status"], c["referenceable"]))
    con.commit()
    rows = con.execute(
        "SELECT id, anomaly_signature, status, referenceable FROM diag_case ORDER BY id").fetchall()
    for r in rows:
        print(dict(zip(["id", "anomaly_signature", "status", "referenceable"], r)))
    con.close()
    print("案例已种入，共 %d 条" % len(CASES))


if __name__ == "__main__":
    main()
