# -*- coding: utf-8 -*-
"""抽样验证: 异常注入是否按预期显现 + 漏斗单调性 + 口径正确性"""
import os
import sqlite3

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ad_review.db")
CUR = "2026-08-10", "2026-08-16"   # 本周(复盘基准)
PRE = "2026-08-03", "2026-08-09"   # 上周
YDAY = "2026-08-17"

con = sqlite3.connect(DB)


def agg(cid, a, b):
    r = con.execute("""SELECT COALESCE(SUM(spend),0),COALESCE(SUM(impressions),0),
        COALESCE(SUM(note_clicks),0),COALESCE(SUM(button_clicks),0),
        COALESCE(SUM(open_msg),0),COALESCE(SUM(lead_cnt),0)
        FROM daily_metric WHERE customer_id=? AND date BETWEEN ? AND ?""", (cid, a, b)).fetchone()
    sp, im, cl, bt, op, ld = r
    m = {"spend": sp, "im": im, "cl": cl, "bt": bt, "op": op, "ld": ld}
    m["cpm"] = sp / im * 1000 if im else 0
    m["ctr"] = cl / im if im else 0
    m["btnr"] = bt / cl if cl else 0
    m["openr"] = op / bt if bt else 0
    m["leadr"] = ld / op if op else 0
    m["open_cost"] = sp / op if op else None
    m["lead_cost"] = sp / ld if ld else None
    return m


def active_plans(cid, day):
    return con.execute(
        "SELECT COUNT(*) FROM plan WHERE customer_id=? AND created_date<=? "
        "AND (stopped_date IS NULL OR stopped_date>?)", (cid, day, day)).fetchone()[0]


def pct(x): return f"{x:+.0%}" if x == x and x is not None else "-"


cid_of = dict(con.execute("SELECT name,id FROM customer").fetchall())
target_of = dict(con.execute("SELECT id,optimize_target FROM customer").fetchall())

print("=" * 110)
print("【异常注入客户 · 本周 vs 上周】（每周7天；监控基准=08-17）")
print("=" * 110)
hdr = f"{'客户':<8}{'目标':<5}{'消耗Δ':>7}{'CPMΔ':>7}{'CTRΔ':>7}{'按钮率Δ':>8}{'开口率Δ':>8}{'留资率Δ':>8}{'开口成本Δ':>9}{'留资成本Δ':>9}{'计划数':>7}{'昨日留资成本':>10}"
print(hdr)

for name in ["悦颜美容SPA", "银龄声乐课堂", "拾光婚纱影像", "星光KTV", "启航留学工作室",
             "洁到家家政", "云栖度假酒店", "素人写真馆", "巅峰密室", "半夏映画"]:
    cid = cid_of[name]
    c, p = agg(cid, *CUR), agg(cid, *PRE)
    tg = target_of[cid]
    oc = pct((c["open_cost"] / p["open_cost"] - 1)) if (tg == "open" and p["open_cost"]) else "-"
    lc = pct((c["lead_cost"] / p["lead_cost"] - 1)) if (tg == "lead" and p["lead_cost"]) else "-"
    print(f"{name:<8}{'开口' if tg=='open' else '留资':<4}"
          f"{pct(c['spend']/p['spend']-1) if p['spend'] else '-':>7}"
          f"{pct(c['cpm']/p['cpm']-1) if p['cpm'] else '-':>7}"
          f"{pct(c['ctr']/p['ctr']-1) if p['ctr'] else '-':>7}"
          f"{pct(c['btnr']/p['btnr']-1) if p['btnr'] else '-':>8}"
          f"{pct(c['openr']/p['openr']-1) if p['openr'] else '-':>8}"
          f"{pct(c['leadr']/p['leadr']-1) if p['leadr'] else '-':>8}"
          f"{oc:>9}{lc:>9}"
          f"{active_plans(cid, PRE[1])}→{active_plans(cid, CUR[1]):>3}"
          f"{(agg(cid, YDAY, YDAY)['lead_cost'] or 0):>10.0f}")

print()
print("=" * 110)
print("【正常客户抽样（应有小噪声、无显著异动）】")
print("=" * 110)
print(hdr)
for name in ["码上AI学堂", "欢乐水世界", "静舍美甲"]:
    cid = cid_of[name]
    c, p = agg(cid, *CUR), agg(cid, *PRE)
    tg = target_of[cid]
    oc = pct((c["open_cost"] / p["open_cost"] - 1)) if (tg == "open" and p["open_cost"]) else "-"
    lc = pct((c["lead_cost"] / p["lead_cost"] - 1)) if (tg == "lead" and p["lead_cost"]) else "-"
    print(f"{name:<8}{'开口' if tg=='open' else '留资':<4}"
          f"{pct(c['spend']/p['spend']-1):>7}{pct(c['cpm']/p['cpm']-1):>7}"
          f"{pct(c['ctr']/p['ctr']-1):>7}{pct(c['btnr']/p['btnr']-1):>8}"
          f"{pct(c['openr']/p['openr']-1):>8}{pct(c['leadr']/p['leadr']-1):>8}"
          f"{oc:>9}{lc:>9}"
          f"{active_plans(cid, PRE[1])}→{active_plans(cid, CUR[1]):>3}"
          f"{(agg(cid, YDAY, YDAY)['lead_cost'] or 0):>10.0f}")

print()
print("=" * 110)
print("【数据完整性 / 全局校验】")
print("=" * 110)
# 1 漏斗单调性
bad = con.execute("""SELECT COUNT(*) FROM daily_metric
    WHERE note_clicks>impressions OR button_clicks>note_clicks
    OR open_msg>button_clicks OR lead_cnt>open_msg""").fetchone()[0]
print(f"1. 漏斗单调性违例: {bad} 行（应为0）")
# 2 缺失场景
for name, exp in [("山野民宿", "缺3天(08-05~07)"), ("枕水人家客栈", "缺整周(08-10~17)")]:
    cid = cid_of[name]
    days = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM daily_metric WHERE customer_id=? ORDER BY date", (cid,))]
    missing = sorted(set(f"2026-08-{x:02d}" for x in range(10, 18)) - set(days))
    print(f"2. {name}（{exp}）: 本周实际有数日期 {len([x for x in days if '2026-08-1' in x and int(x[-2:])>=10])}/8")
# 3 版位只含 feed/search
pl = con.execute("SELECT placement, COUNT(*) FROM daily_metric GROUP BY placement").fetchall()
print(f"3. 版位分布: {dict(pl)}（应只有 feed/search）")
# 4 优化目标分布
tg = con.execute("SELECT optimize_target, COUNT(*) FROM customer GROUP BY optimize_target").fetchall()
print(f"4. 优化目标分布: {dict(tg)}")
# 5 数据天数覆盖
days = con.execute("SELECT MIN(date), MAX(date), COUNT(DISTINCT date) FROM daily_metric").fetchone()
print(f"5. 日期范围: {days[0]} ~ {days[1]}（{days[2]} 天）")
# 6 目标成本与实际成本量级对照（留资客户）
print("6. 目标成本 vs 实际周均成本（抽查3个留资客户）:")
for name in ["悦颜美容SPA", "云栖度假酒店", "素人写真馆"]:
    cid = cid_of[name]
    t = con.execute("SELECT target_cost FROM customer WHERE id=?", (cid,)).fetchone()[0]
    m = agg(cid, *PRE)
    print(f"   {name}: 目标留资成本 ¥{t:.0f} | 上周实际 ¥{(m['lead_cost'] or 0):.0f}")
con.close()
