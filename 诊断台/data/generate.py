# -*- coding: utf-8 -*-
"""诊断台 · 模拟数据生成器
确定性生成（固定种子），参数化场景注入，输出 SQLite。
用法: python3 generate.py [--db ad_review.db]
"""
import math
import os
import random
import sqlite3
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from customers import CUSTOMERS, SECTOR_PROFILE
from scenarios import SCENARIOS, SIM_VERSION, START_DATE, END_DATE

SEED = 42
D0 = date.fromisoformat(START_DATE)
D1 = date.fromisoformat(END_DATE)
ALL_DAYS = [(D0 + timedelta(days=i)).isoformat() for i in range((D1 - D0).days + 1)]


def sround(x, rng):
    """随机取整（无偏低计数）: 3.7 → 3 或 4，概率 0.7/0.3"""
    f = math.floor(x)
    return f + (1 if rng.random() < x - f else 0)

TITLE_ANGLE = ["避坑指南", "真实体验", "3天速成", "天花板级", "宠粉福利价", "新手必看",
               "ins风", "宝藏小店", "限时团", "口碑爆款", "深度测评", "老客推荐"]
PLAN_SUFFIX = ["主推", "拓展", "测试", "加量", "日常", "新品", "场景", "复投"]


def d(s): return date.fromisoformat(s)


def gen_customer_metrics(cust_id, name, sector, cats, rng, scn, optimize_target, target_cost):
    """生成一个客户的 plan/note/每日明细。返回 (plans, notes, rows)"""
    prof = SECTOR_PROFILE[sector]
    cpm0, ctr0, btn0, open0, lead0, wknd = prof
    # 客户级资质抖动（好客户/差客户）
    k = rng.uniform(0.85, 1.2)
    ctr_c, btn_c, open_c, lead_c = ctr0 * k, btn0 * k, open0 * k, lead0 * k
    # 成本量级校准: 使目标口径的周均成本 ≈ target_cost
    imp_py = 1000.0 / cpm0  # 每元消耗的曝光
    if optimize_target == "open":
        f = (1.0 / (imp_py * ctr_c * btn_c * open_c)) / target_cost
        open_c = max(0.02, min(open_c * f, 0.9))
    else:
        f = (1.0 / (imp_py * ctr_c * btn_c * open_c * lead_c)) / target_cost
        lead_c = max(0.02, min(lead_c * f, 0.9))

    scn_stop = next((s for s in scn if s["type"] == "stop_plans"), None)
    scn_good = next((s for s in scn if s["type"] == "add_good_plan"), None)
    scn_weak = next((s for s in scn if s["type"] == "weak_new_notes"), None)
    rate_scn = [s for s in scn if s["type"] == "rate_mult"]
    imp_scn = [s for s in scn if s["type"] == "metric_mult" and s["metric"] == "impressions"]
    drop_dates = set()
    for s in scn:
        if s["type"] == "drop_days":
            if "dates" in s:
                drop_dates |= set(s["dates"])
            else:
                a, b = d(s["range"][0]), d(s["range"][1])
                cur = a
                while cur <= b:
                    drop_dates.add(cur.isoformat()); cur += timedelta(days=1)

    # ---- 计划 ----
    plans = []   # dict: id,cat_idx,placement,created,stopped,budget,ramp
    n_plans = rng.randint(5, 9)
    for i in range(n_plans):
        pre = rng.random() < 0.7  # 70% 期初即存在
        created = (D0 - timedelta(days=rng.randint(10, 60))) if pre else \
                  (D0 + timedelta(days=rng.randint(0, 40)))
        plans.append({
            "cat": cats[i % len(cats)],
            "placement": "search" if rng.random() < 0.35 else "feed",
            "created": created, "stopped": None,
            "budget": rng.uniform(150, 420) * (1.8 if rng.random() < 0.3 else 1.0),
        })
    # 普通自然停投（素材轮换），保留少量
    for p in plans:
        if rng.random() < 0.12:
            p["stopped"] = D0 + timedelta(days=rng.randint(15, 45))
    # 场景: 停投 N 个（基建缩减）
    if scn_stop:
        alive = sorted([p for p in plans if p["stopped"] is None],
                       key=lambda p: p["created"])
        for p in alive[:scn_stop["count"]]:
            p["stopped"] = d(scn_stop["start"])
    # 场景: 新增优质计划（正向）
    if scn_good:
        plans.append({"cat": cats[0], "placement": "feed", "created": d(scn_good["start"]),
                      "stopped": None, "budget": scn_good["budget"], "good": True})

    # ---- 笔记 ----
    notes = []   # dict: plan_idx,cat,title,form,created,stopped,quality
    def add_note(pi, created, quality=1.0, btn_q=None):
        cat = plans[pi]["cat"]
        notes.append({
            "plan": pi, "cat": cat,
            "title": f"{cat}{rng.choice(TITLE_ANGLE)}",
            "form": "视频" if rng.random() < 0.45 else "图文",
            "created": created, "stopped": None, "quality": quality, "btn_q": btn_q,
        })
        return notes[-1]

    for pi, p in enumerate(plans):
        n_notes = rng.randint(2, 4)
        for j in range(n_notes):
            created = p["created"] + timedelta(days=rng.randint(0, 18))
            if created > D1:
                created = p["created"] + timedelta(days=rng.randint(0, 6))
            q = rng.uniform(0.7, 1.35) * (scn_good["quality"] if p.get("good") else 1.0)
            add_note(pi, created, quality=q)
    if scn_weak:  # 场景: 新增低质笔记（铺多个存活计划，抢高消耗份额但转化差）
        alive = [i for i, p in enumerate(plans) if not p["stopped"]]
        hosts = alive[:4] if len(alive) >= 4 else alive
        for j in range(scn_weak["count"]):
            add_note(hosts[j % len(hosts)], d(scn_weak["start"]),
                     quality=scn_weak.get("quality", 1.6), btn_q=scn_weak["button_factor"])

    # ---- 每日明细 ----
    rows = []
    for day in ALL_DAYS:
        if day in drop_dates:
            continue
        dd = d(day)
        wk = wknd if dd.weekday() >= 5 else 1.0
        for pi, p in enumerate(plans):
            if p["created"] > dd or (p["stopped"] and dd >= p["stopped"]):
                continue
            age = (dd - p["created"]).days
            ramp = {0: 0.35, 1: 0.6, 2: 0.85}.get(age, 1.0)
            budget_day = p["budget"] * wk * ramp * rng.uniform(0.82, 1.08)
            active = [n for n in notes if n["plan"] == pi and n["created"] <= dd
                      and not (n["stopped"] and dd >= n["stopped"])]
            if not active:
                continue
            wsum = sum(n["quality"] for n in active)
            for n in active:
                spend = budget_day * n["quality"] / wsum * rng.uniform(0.85, 1.15)
                if spend < 5:
                    continue
                search = p["placement"] == "search"
                boost = 1.4 if p.get("good") else 1.0  # 优质计划转化加成
                cpm = cpm0 * rng.uniform(0.9, 1.1) * (1.6 if search else 1.0)
                imp_f = 1.0
                for s in imp_scn:  # 场景: 曝光缩水 → CPM上涨（消耗不变）
                    if s["start"] <= day <= s["end"] and s.get("placement", p["placement"]) == p["placement"]:
                        imp_f *= s["factor"]
                impressions = int(spend / cpm * 1000 * imp_f)
                ctr = ctr_c * (1.8 if search else 1.0) * rng.uniform(0.88, 1.12)
                for s in rate_scn:
                    if s["rate"] == "ctr" and s["start"] <= day <= s["end"]:
                        ctr *= s["factor"]
                clicks = sround(impressions * ctr * boost, rng)
                br = btn_c * (n["btn_q"] or 1.0) * rng.uniform(0.85, 1.15)
                buttons = sround(clicks * br, rng)
                orate = open_c * rng.uniform(0.88, 1.12)
                for s in rate_scn:
                    if s["rate"] == "open_rate" and s["start"] <= day <= s["end"]:
                        orate *= s["factor"]
                opens = sround(buttons * orate * boost, rng)
                lrate = lead_c * rng.uniform(0.85, 1.15)
                for s in rate_scn:
                    if s["rate"] == "lead_rate" and s["start"] <= day <= s["end"]:
                        lrate *= s["factor"]
                leads = sround(opens * lrate, rng)
                # 漏斗单调保护
                buttons = min(buttons, clicks); opens = min(opens, buttons); leads = min(leads, opens)
                rows.append((day, cust_id, p["cat"], p["placement"], pi, n,
                             spend, impressions, clicks, buttons, opens, leads))
    return plans, notes, rows


def main():
    db_path = sys.argv[sys.argv.index("--db") + 1] if "--db" in sys.argv else \
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "ad_review.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    schema = open(os.path.join(os.path.dirname(db_path), "..", "db", "schema.sql")).read()
    con = sqlite3.connect(db_path)
    con.executescript(schema)
    cur = con.cursor()

    # 维度入库
    industries, sectors, cats_all = {}, {}, {}
    for _, ind, sec, cats, _, _ in CUSTOMERS:
        industries.setdefault(ind, len(industries) + 1)
        sectors.setdefault((ind, sec), len(sectors) + 1)
        for c in cats:
            cats_all.setdefault(c, len(cats_all) + 1)
    cur.executemany("INSERT INTO industry VALUES(?,?)", [(i, n) for n, i in industries.items()])
    cur.executemany("INSERT INTO sector VALUES(?,?,?)",
                    [(sid, industries[ind], sec) for (ind, sec), sid in sectors.items()])
    cur.executemany("INSERT INTO category VALUES(?,?)", [(i, n) for n, i in cats_all.items()])

    plan_rows, note_rows, dm_rows = [], [], []
    pid = nid = 0
    for cid, (name, ind, sec, cats, target, tcost) in enumerate(CUSTOMERS, 1):
        rng = random.Random(f"{SEED}-{name}")
        cur.execute("INSERT INTO customer(id,name,sector_id,optimize_target,target_cost,status) "
                    "VALUES(?,?,?,?,?,?)",
                    (cid, name, sectors[(ind, sec)], target, tcost, "active"))
        cur.executemany("INSERT INTO customer_category VALUES(?,?)",
                        [(cid, cats_all[c]) for c in cats])
        scn = SCENARIOS.get(name, [])
        plans, notes, rows = gen_customer_metrics(cid, name, sec, cats, rng, scn, target, tcost)
        for pi, p in enumerate(plans):
            pid += 1; p["id"] = pid
            plan_rows.append((pid, cid, cats_all[p["cat"]],
                              f"{p['cat']}·{pid:03d}{rng.choice(PLAN_SUFFIX)}",
                              p["placement"], p["created"].isoformat(),
                              "停投" if p["stopped"] else "在投",
                              round(p["budget"], 2),
                              p["stopped"].isoformat() if p["stopped"] else None))
        for n in notes:
            nid += 1; n["id"] = nid
            note_rows.append((nid, cid, cats_all[n["cat"]], plans[n["plan"]]["id"],
                              n["title"], n["form"], n["created"].isoformat(),
                              "停投" if n["stopped"] else "在投",
                              n["stopped"].isoformat() if n["stopped"] else None))
        for (day, c, cat, pl, pi, n, spend, imp, cl, bt, op, ld) in rows:
            dm_rows.append((day, c, cats_all[cat], pl, plans[pi]["id"], n["id"],
                            round(spend, 2), imp, cl, bt, op, ld, "sim", SIM_VERSION))

    cur.executemany("INSERT INTO plan VALUES(?,?,?,?,?,?,?,?,?)", plan_rows)
    cur.executemany("INSERT INTO note VALUES(?,?,?,?,?,?,?,?,?)", note_rows)
    cur.executemany("INSERT INTO daily_metric VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", dm_rows)
    con.commit()

    print(f"DB: {db_path}")
    print(f"customers={len(CUSTOMERS)} plans={len(plan_rows)} notes={len(note_rows)} "
          f"daily_rows={len(dm_rows)} sim_version={SIM_VERSION}")
    con.close()


if __name__ == "__main__":
    main()
