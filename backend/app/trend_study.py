"""Predefined trend intersections, date-cluster sign-flip and BH correction.

Input is a frozen event study; future prices cannot determine trend labels.
"""
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from random import Random
import argparse, json, math, time
from sqlalchemy import select
from .db import Session, ProviderObservation

def trend(prices, calendar, day):
    if day not in calendar: return None
    i=calendar.index(day)
    if i<59: return None
    days=calendar[i-59:i+1]
    if any(d not in prices or prices[d] is None or float(prices[d])<=0 for d in days): return None
    values=[float(prices[d]) for d in days]
    short=sum(values[-20:])/20; long=sum(values)/60
    return 'bull' if short>long else 'nonbull'

def bh(pvalues):
    order=sorted(range(len(pvalues)),key=lambda i:pvalues[i]);out=[1.0]*len(order);q=1.0
    for rank in range(len(order),0,-1):
        i=order[rank-1];q=min(q,pvalues[i]*len(order)/rank);out[i]=q
    return out

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=int,default=3600);parser.add_argument('--draws',type=int,default=100000);args=parser.parse_args()
    data=json.loads(Path('research/backtest_curve_130_110.json').read_text(encoding='utf8'))
    bench={m:json.loads(Path(p).read_text(encoding='utf8'))['days'] for m,p in [('TWSE','research/benchmark/taiex_202603_202611.json'),('TPEx','research/benchmark/tpex_202603_202611.json')]}
    calendars={m:sorted(date.fromisoformat(d) for d in rows) for m,rows in bench.items()}
    indices={m:{date.fromisoformat(d):v['close'] for d,v in rows.items()} for m,rows in bench.items()}
    with Session() as db:
        rows=db.scalars(select(ProviderObservation).where(ProviderObservation.provider=='FinMind',ProviderObservation.trading_date>=date(2026,3,1),ProviderObservation.trading_date<=date(2026,11,3))).all()
        prices=defaultdict(dict)
        for r in rows: prices[r.code][r.trading_date]=r.close
    combos=[];missing=0;warm=0
    for row in data['results']:
        groups=defaultdict(list)
        for event in row['events']:
            day=date.fromisoformat(event['signal_date']);market=event['market'];code=event['code']
            stocktrend=trend(prices[code],calendars[market],day);indextrend=trend(indices[market],calendars[market],day)
            if not stocktrend or not indextrend:missing+=1;continue
            if event['warmup']:warm+=1
            groups[(stocktrend,indextrend)].append(event)
        for a in ('bull','nonbull'):
            for b in ('bull','nonbull'):
                events=groups[(a,b)]; clusters=defaultdict(float)
                for e in events:clusters[e['signal_date']]+=float(e['excess_pct'])
                combos.append({'threshold':row['threshold'],'holding_days':row['holding_days'],'stock_trend':a,'index_trend':b,
                  'events':len(events),'signal_dates':len(clusters),'warmup_events':sum(bool(e['warmup']) for e in events),
                  'non_warmup_events':sum(not e['warmup'] for e in events),
                  'mean_excess_pp':sum(float(e['excess_pct']) for e in events)/len(events) if events else None,
                  '_cluster_sums':list(clusters.values())})
    # Minimum 10 date clusters. All 320 planned tests count in multiplicity.
    rng=Random(20261104);started=time.monotonic();deadline=started+max(args.seconds,1);draws=0;next_checkpoint=started
    for c in combos:c['_extreme']=0
    while draws<args.draws and time.monotonic()<deadline:
        for c in combos:
            v=c['_cluster_sums']
            if len(v)<10: continue
            observed=abs(sum(v)); randomized=abs(sum(x if rng.getrandbits(1) else -x for x in v))
            if randomized>=observed-1e-12:c['_extreme']+=1
        draws+=1
        if time.monotonic()>=next_checkpoint:
            Path('research/trend_study_progress.json').write_text(json.dumps({'status':'running','draws':draws,'elapsed_seconds':round(time.monotonic()-started),'target_seconds':args.seconds,'updated_at':datetime.now(timezone.utc).isoformat()}),encoding='utf8')
            next_checkpoint=time.monotonic()+30
    for c in combos:
        c['p_unadjusted']=(c['_extreme']+1)/(draws+1) if len(c['_cluster_sums'])>=10 and draws else None
    q=bh([c['p_unadjusted'] if c['p_unadjusted'] is not None else 1 for c in combos])
    for c,value in zip(combos,q):
        c['bh_q']=value if c['p_unadjusted'] is not None else None
        c.pop('_cluster_sums');c.pop('_extreme')
    report={'generated_at':datetime.now(timezone.utc).isoformat(),'source_generated_at':data['generated_at'],
      'eligible_stocks':data['eligible_stocks'],'stock_exclusions':data['stock_exclusions'],'draws':draws,
      'preplanned_tests':len(combos),'missing_trend_event_instances':missing,'warmup_event_instances':warm,
      'method':'訊號日收盤可得的個股與所屬市場價格指數 20 日均線與 60 日均線，兩者各分月線大於季線與否；按訊號日期為叢集進行雙側符號翻轉，至少 10 個訊號日，對預定 320 次檢定做 Benjamini–Hochberg FDR 校正。',
      'limitations':['市場和個股資料可能事後修訂，非 point-in-time；現今市值前 500 檔存在存活者偏差',
        '使用初始回測先排除重疊交易的事件；各條件不是獨立回測',
        '日期叢集符號翻轉要求零假設下日期層級對稱，且未處理跨日相關；不是嚴格的風險調整 alpha',
        '選擇閾值和期間並非事先註冊；多重比較調整不能移除資料探勘偏差',
        '暖機期、非完整公司行動、未建模漲跌停與滑價，均可能讓結果失真'], 'results':combos}
    Path('research/trend_study.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    Path('research/trend_study_progress.json').write_text(json.dumps({'status':'completed','draws':draws,'elapsed_seconds':round(time.monotonic()-started),'target_seconds':args.seconds,'updated_at':datetime.now(timezone.utc).isoformat()}),encoding='utf8')
    print(json.dumps({'draws':draws,'tested':sum(c['p_unadjusted'] is not None for c in combos),'q_below_005':sum(c['bh_q'] is not None and c['bh_q']<.05 for c in combos),'non_warmup_events':sum(c['non_warmup_events'] for c in combos)}))

if __name__=='__main__':main()
