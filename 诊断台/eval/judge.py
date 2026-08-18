#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""judge 评分器（端到端 B 层）：用 LLM 按 D1/D2/D3 三档给报告打分

用法:
  python3 eval/judge.py --calibrate          # 校准模式：对 6 份固定报告打分并与 judge_baseline.json 对比
  python3 eval/judge.py --report 拾光婚纱影像 # 单份报告打分
  python3 eval/judge.py --all                # 对 artifacts 全量报告打分，结果存 artifacts/judge_scores.json

评分标准: eval/judge_prompt.md（内联进 system prompt）
前置: DEEPSEEK_API_KEY 环境变量
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent / "agent"
ART = HERE / "artifacts"
sys.path.insert(0, str(AGENT))
from llm import call_deepseek  # noqa: E402

CALIB_NAMES = ["码上AI学堂", "拾光婚纱影像", "枕水人家客栈", "素人写真馆", "银龄声乐课堂", "巅峰密室"]

KEY_LABELS = {
    'customer': '客户', 'industry': '行业', 'sector': '赛道', 'categories': '品类', 'period': '对比周期',
    'version': '版本', 'generated_at': '生成时间', 'overall_status': '整体状态', 'data_status': '数据状态',
    'data_check': '数据完整性检查', 'summary': '本周总结', 'top3': 'Top3要点', 'cur_missing': '本期缺失',
    'prev_missing': '上期缺失', 'top3_detail': '异常详情', 'watchlist': '观察清单',
    'cases': '案例', 'refs': '案例引用', 'n': '数量', 'note': '说明',
    'location': '位置', 'change': '变化幅度', 'weight': '权重', 'direction': '方向', 'direction_note': '方向说明',
    'desc': '描述', 'detail': '明细', 'metric': '指标', 'reason': '原因', 'evidence': '证据', 'level': '层级',
    'similarity_points': '相似点', 'key_differences': '关键差异', 'adopted': '是否采纳',
    'confidence': '置信度', 'metric_name': '指标名', 'cur_value': '本期值', 'prev_value': '上期值',
}
METRIC_LABELS = {
    'CPM': 'CPM(元)', 'CTR': 'CTR', 'CPC': 'CPC(元)', 'button_rate': '按钮率', 'open_rate': '开口率',
    'lead_rate': '留资率', 'lead_cvr': '留资转化率', 'open_cost': '开口成本(元)', 'lead_cost': '留资成本(元)',
    'spend': '消耗(元)', 'impressions': '曝光', 'clicks': '点击', 'opens': '开口数', 'leads': '留资数',
}


def _fmt(v):
    if v is None:
        return '无'
    if isinstance(v, float):
        return f'{v:g}'
    return str(v)


def _fmt_pct(v):
    if v is None:
        return '无'
    try:
        return f'{float(v)*100:+.1f}%'
    except (TypeError, ValueError):
        return str(v)


def _render(v, lines, depth=0):
    pad = '  ' * depth
    if isinstance(v, dict):
        for k, vv in v.items():
            label = KEY_LABELS.get(k, k)
            if isinstance(vv, (dict, list)):
                lines.append(f'{pad}- {label}')
                _render(vv, lines, depth + 1)
            else:
                lines.append(f'{pad}- {label}：{_fmt(vv)}')
    elif isinstance(v, list):
        if not v:
            lines.append(f'{pad}- 无')
        for i, item in enumerate(v, 1):
            pad2 = '  ' * depth
            if isinstance(item, dict):
                lines.append(f'{pad2}- 第{i}项')
                _render(item, lines, depth + 1)
            else:
                lines.append(f'{pad2}- {_fmt(item)}')
    else:
        lines.append(f'{pad}- {_fmt(v)}')


def render_report(d):
    """报告 JSON -> 评委可读文本"""
    out = []
    cover = d['chapters'].get('1_封面', {})
    out.append(f"客户：{cover.get('customer','')} ｜ 整体状态：{d.get('overall_status','')} ｜ "
               f"周期：{cover.get('period','')} ｜ 生成时间：{cover.get('generated_at','')}")
    for ck, cv in d['chapters'].items():
        if ck == '1_封面':
            continue
        num, _, title = ck.partition('_')
        out.append('')
        out.append(f'## {num}. {title}')
        lines = []
        if ck == '3_指标与趋势' and isinstance(cv, dict):
            cur, prev, chg = cv.get('metrics_cur', {}), cv.get('metrics_prev', {}), cv.get('metrics_change', {})
            out.append('| 指标 | 本期 | 上期 | 环比 |')
            out.append('| --- | --- | --- | --- |')
            for k in (list(cur.keys()) or list(prev.keys())):
                g = chg.get(k)
                out.append(f"| {METRIC_LABELS.get(k, k)} | {_fmt(cur.get(k, '—'))} | "
                           f"{_fmt(prev.get(k, '—'))} | {_fmt_pct(g) if g is not None else '—'} |")
            for extra, t in (('funnel', '漏斗'), ('trend_14d', '近14天趋势（注意：这是逐日数据，不是周值）')):
                if cv.get(extra):
                    out.append(f'\n{t}')
                    _render(cv[extra], out, 0)
        else:
            _render(cv, lines, 0)
            out.extend(lines)
    return '\n'.join(out)


def judge_one(name, d, prompt, votes=3):
    """单份报告打分：跑 votes 次取每维度中位数，消除 LLM 边界波动"""
    text = render_report(d)
    msgs = [{"role": "system", "content": prompt},
            {"role": "user", "content": f"请给下面这份报告打分：\n\n{text}"}]
    runs, cost = [], 0.0
    for _ in range(votes):
        resp = call_deepseek(msgs, temperature=0, max_tokens=2500, json_mode=True)
        cost += resp['cost_yuan']
        raw = resp['text'].strip()
        try:
            s = raw[raw.index('{'): raw.rindex('}') + 1]
            obj = json.loads(s)
            runs.append({"scores": {k: int(obj[k]) for k in ("D1", "D2", "D3")},
                         "reasons": obj.get("reasons", {})})
        except Exception:
            continue
    if not runs:
        return {"error": "全部轮次 JSON 解析失败", "cost_yuan": cost}
    scores = {k: sorted(r["scores"][k] for r in runs)[len(runs) // 2] for k in ("D1", "D2", "D3")}
    spread = {k: max(r["scores"][k] for r in runs) - min(r["scores"][k] for r in runs)
              for k in ("D1", "D2", "D3")}
    # 理由取与中位数分数最接近的一轮
    best = min(runs, key=lambda r: sum(abs(r["scores"][k] - scores[k]) for k in scores))
    return {"scores": scores, "spread": spread,
            "runs": [r["scores"] for r in runs],
            "reasons": best["reasons"], "cost_yuan": cost}


def load(name):
    return json.loads((ART / f'{name}.json').read_text(encoding='utf-8'))


def calibrate(prompt):
    baseline = json.loads((HERE / 'judge_baseline.json').read_text(encoding='utf-8'))
    results, diffs = {}, {"D1": 0, "D2": 0, "D3": 0}
    total = {"D1": 0, "D2": 0, "D3": 0}
    print('== judge 校准：逐份打分（人工基准分已预先写死在 judge_baseline.json）==')
    for n in CALIB_NAMES:
        r = judge_one(n, load(n), prompt)
        if 'error' in r:
            print(f'[{n}] 失败: {r["error"]}')
            continue
        sc, base = r['scores'], baseline[n]
        results[n] = r
        marks = []
        for k in ('D1', 'D2', 'D3'):
            total[k] += 1
            if sc[k] != base[k]:
                diffs[k] += 1
                marks.append(f'{k}: judge={sc[k]} 基准={base[k]} ←')
            else:
                marks.append(f'{k}: {sc[k]}')
        print(f'[{n}] {" | ".join(marks)}')
        for k in ('D1', 'D2', 'D3'):
            print(f'    {k} 理由: {r["reasons"].get(k, "")[:120]}')
        time.sleep(0.5)
    print('\n== 分歧率（judge 与基准不一致的比例，>30% 需回炉）==')
    for k in ('D1', 'D2', 'D3'):
        rate = diffs[k] / total[k] * 100 if total[k] else 0
        verdict = '回炉重写评分标准' if rate > 30 else '通过'
        print(f'  {k}: {diffs[k]}/{total[k]} = {rate:.0f}%  {verdict}')
    (ART / 'judge_calibration.json').write_text(
        json.dumps({"results": results, "diffs": diffs, "total": total}, ensure_ascii=False, indent=1),
        encoding='utf-8')
    print('\n结果已存 artifacts/judge_calibration.json')
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--calibrate', action='store_true')
    ap.add_argument('--report')
    ap.add_argument('--all', action='store_true')
    args = ap.parse_args()
    prompt = (HERE / 'judge_prompt.md').read_text(encoding='utf-8')
    if args.calibrate:
        calibrate(prompt)
    elif args.report:
        r = judge_one(args.report, load(args.report), prompt)
        print(json.dumps(r, ensure_ascii=False, indent=1))
    elif args.all:
        scores = {}
        for p in sorted(ART.glob('*.json')):
            if p.stem in ('eval_results', 'judge_scores', 'judge_calibration'):
                continue
            try:
                d = json.loads(p.read_text(encoding='utf-8'))
                if 'chapters' not in d:
                    continue
            except Exception:
                continue
            print(f'judging {p.stem} ...')
            scores[p.stem] = judge_one(p.stem, d, prompt)
            time.sleep(0.5)
        (ART / 'judge_scores.json').write_text(
            json.dumps(scores, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'完成 {len(scores)} 份 -> artifacts/judge_scores.json')
    else:
        ap.print_help()


if __name__ == '__main__':
    main()
