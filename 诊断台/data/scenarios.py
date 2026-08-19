# -*- coding: utf-8 -*-
"""场景注入配置（参数化：哪个客户、哪个日期窗、哪个维度、变多少）。

数据周期: 2026-06-24 ~ 2026-08-17（55 天）
· 周度复盘对比基准: 本周 08-10~08-16 vs 上周 08-03~08-09
· 日常监控: 昨日=08-17 vs 近14天加权基准

注入类型:
  stop_plans      停投 N 个计划（基建缩减 → 消耗下降）
  metric_mult     时间窗内某指标乘以 factor（可限 placement/plan 关键字）
  rate_mult       时间窗内某转化率乘以 factor（沿漏斗向下重算）
  drop_days       删除指定日期的数据行（数据缺失）
  add_good_plan   日期起新增高质量计划+笔记（正向: 消耗升+成本降）
  weak_new_notes  日期起新增笔记质量差（按钮点击率低 → 拖累率类指标）
"""

SCENARIOS = {
    # E02 基建缩减→消耗下降（单点）
    "悦颜美容SPA": [
        {"type": "stop_plans", "count": 3, "start": "2026-08-10"},
    ],
    # E03 CPM 上涨（信息流量价变贵，消耗不变曝光缩水）
    "银龄声乐课堂": [
        {"type": "metric_mult", "start": "2026-08-10", "end": "2026-08-16",
         "metric": "impressions", "factor": 0.68, "placement": "feed"},
    ],
    # E04 CTR 下降（笔记点击率恶化，无素材标签字段 → 只能待验证假设）
    "拾光婚纱影像": [
        {"type": "rate_mult", "start": "2026-08-10", "end": "2026-08-16",
         "rate": "ctr", "factor": 0.60},
    ],
    # E05 新笔记质量差 → 按钮点击率下降
    "星光KTV": [
        {"type": "weak_new_notes", "start": "2026-08-08", "count": 8, "button_factor": 0.15, "quality": 2.2},
    ],
    # E06 私信开口率下降
    "启航留学工作室": [
        {"type": "rate_mult", "start": "2026-08-10", "end": "2026-08-16",
         "rate": "open_rate", "factor": 0.58},
    ],
    # E07 链路留资率下降（话术/首响问题）
    "洁到家家政": [
        {"type": "rate_mult", "start": "2026-08-10", "end": "2026-08-16",
         "rate": "lead_rate", "factor": 0.52},
    ],
    # E08 复合: CPM上涨 + CTR下降 → 留资成本大涨
    "云栖度假酒店": [
        {"type": "metric_mult", "start": "2026-08-10", "end": "2026-08-16",
         "metric": "impressions", "factor": 0.72},
        {"type": "rate_mult", "start": "2026-08-10", "end": "2026-08-16",
         "rate": "ctr", "factor": 0.75},
    ],
    # E09 正向: 客户主动扩量(基建增加) + 新组合效果好 → 消耗升+成本降
    "素人写真馆": [
        {"type": "add_good_plan", "start": "2026-08-10", "quality": 1.9, "budget": 900},
    ],
    # E10 指标冲突: CTR 降但开口率升 → 开口成本下降
    "巅峰密室": [
        {"type": "rate_mult", "start": "2026-08-10", "end": "2026-08-16",
         "rate": "ctr", "factor": 0.7},
        {"type": "rate_mult", "start": "2026-08-10", "end": "2026-08-16",
         "rate": "open_rate", "factor": 1.6},
    ],
    # E11a 数据缺失: 周中缺 3 天
    "山野民宿": [
        {"type": "drop_days", "dates": ["2026-08-05", "2026-08-06", "2026-08-07"]},
    ],
    # E11b 数据缺失: 本周整周缺失（完整性拦截 → 拒绝归因）
    "枕水人家客栈": [
        {"type": "drop_days", "range": ["2026-08-10", "2026-08-17"]},
    ],
    # 历史周异常（已自行恢复）——检验 Agent 不会把旧异常当本周问题
    "半夏映画": [
        {"type": "metric_mult", "start": "2026-07-27", "end": "2026-08-02",
         "metric": "impressions", "factor": 0.7},
    ],
}

SIM_VERSION = "sim-v1.0.0"
# 数据窗口随真实时间滚动：结束日 = max(原固定末日, 今天)，保证「今日」默认窗口有数据
from datetime import date as _date  # noqa: E402
END_DATE = max("2026-08-17", _date.today().isoformat())
START_DATE = "2026-06-24"
