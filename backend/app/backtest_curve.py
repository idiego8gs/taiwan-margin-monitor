"""Exploratory threshold-by-horizon study; resample signal dates, not trades."""
import json, random
from collections import defaultdict
from datetime import date
from pathlib import Path
from .backtest_alpha import run, parse_benchmark
from .db import Session

def date_cluster_interval(events, samples=2000):
    groups = defaultdict(list)
    for event in events:
        groups[str(event['signal_date'])].append(float(event['excess_pct']))
    if len(groups) < 5:
        return None
    clusters = list(groups.values())
    rng = random.Random(431)
    means = []
    for _ in range(samples):
        chosen = [rng.choice(clusters) for _ in clusters]
        means.append(sum(map(sum, chosen)) / sum(map(len, chosen)))
    means.sort()
    return [means[int(samples * .025)], means[int(samples * .975) - 1]]

def main():
    universe=json.loads(Path('research/top500_20261103/universe.json').read_text(encoding='utf8'))
    twse=parse_benchmark(json.loads(Path('research/benchmark/taiex_202607_202611.json').read_text(encoding='utf8')))
    tpex=parse_benchmark(json.loads(Path('research/benchmark/tpex_202607_202611.json').read_text(encoding='utf8')))
    with Session() as db:
        result=run(db,universe,twse,date(2026,7,8),date(2026,11,3),date(2026,8,31),tpex,(130,125,120,110),range(1,21))
    for row in result['results']:
        row['date_cluster_bootstrap_95_pct'] = date_cluster_interval(row['events'])
    result['bootstrap_note'] = '以訊號日期為叢集重抽 2000 次；未校正 80 組多重比較，不代表風險調整 alpha 或因果效果。'
    Path('research/backtest_curve_130_110.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf8')
    result.pop('stocks',None)
    for row in result['results']: row.pop('events',None)
    Path('github-pages/curve.json').write_text(json.dumps(result,ensure_ascii=False,default=str),encoding='utf8')
    print(json.dumps({'eligible_stocks':result['eligible_stocks'],'exclusions':result['stock_exclusions']},ensure_ascii=False))

if __name__=='__main__':main()
