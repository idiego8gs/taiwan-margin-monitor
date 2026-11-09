"""Exploratory matched factor validation, built entirely from local snapshots.

Historical availability and constituents are unverified: no true PIT/OOS claim.
See docs/FACTOR_PROTOCOL.md before interpreting any result.
"""
from __future__ import annotations
from bisect import bisect_right
from collections import Counter,defaultdict,deque
from datetime import date,datetime,time,timezone
from math import log
from pathlib import Path
from statistics import mean,median,stdev
from zoneinfo import ZoneInfo
import argparse,gzip,json
from sqlalchemy import select
from .calculation import load_calendar
from .db import Session,ProviderObservation
from .factor_statistics import Snapshot,match_on_date,moving_block_inference,adjust_pvalues

THRESHOLDS=(130,125,120,110)
HORIZONS=(1,5,10)
GROUPS=('all','bull_bull','bull_nonbull','nonbull_bull','nonbull_nonbull')
CALIPERS={'prior_return_5':5.0,'volatility_20':2.0,'log_turnover_20':1.0}
FEE=.001425;TAX=.003;SLIPPAGE=.001

def net_return(entry,exit,fee=FEE,tax=TAX,slippage=SLIPPAGE):
    if entry is None or exit is None or entry<=0 or exit<=0:return None
    return 100*((float(exit)*(1-fee-tax)*(1-slippage))/(float(entry)*(1+fee)*(1+slippage))-1)

def valid_model(model,mode):
    if not model or model['maintenance'] is None:return False
    return (not model['warmup']) if mode=='strict' else float(model['sensitivity_pct'])<=5

def crossing(now,prev,threshold,mode):
    return (valid_model(now,mode) and valid_model(prev,mode) and now['segment']==prev['segment'] and
            float(prev['maintenance'])>threshold>=float(now['maintenance']))

def market_regimes(calendar,bench):
    output={};window=deque(maxlen=60)
    for day in calendar:
        x=bench.get(day.isoformat())
        if not x:window.clear();continue
        try:close=float(x['close'])
        except (KeyError,TypeError,ValueError):window.clear();continue
        if close<=0:window.clear();continue
        window.append(close)
        if len(window)==60:output[day]='bull' if mean(list(window)[-20:])>mean(window) else 'nonbull'
    return output

def make_feature_rows(calendar,quality,model_path,db):
    """Features use only day t and earlier; forward outcome fields are separate."""
    model=defaultdict(dict)
    with gzip.open(model_path,'rt',encoding='utf8') as f:
        for line in f:
            x=json.loads(line);model[x['code']][date.fromisoformat(x['date'])]=x
    indices={d:i for i,d in enumerate(calendar)}
    regimes={}
    for market,name in [('TWSE','taiex'),('TPEx','tpex')]:
        bench=json.loads(Path(f'research/benchmark/{name}_202110_202611.json').read_text(encoding='utf8'))['days']
        regimes[market]=market_regimes(calendar,bench)
    by_day=defaultdict(list);report=Counter()
    universe=json.loads(Path('research/top500_20261103/universe.json').read_text(encoding='utf8'))
    for stock in universe['items']:
        code=stock['code'];market=stock['market'];detail=quality['details'].get(code,{})
        if not detail.get('valid_model_days') or market not in regimes:continue
        source=db.scalars(select(ProviderObservation).where(ProviderObservation.provider=='FinMind',ProviderObservation.code==code).order_by(ProviderObservation.trading_date)).all()
        price={r.trading_date:r for r in source};actions=sorted(date.fromisoformat(x) for x in detail.get('action_dates',[]))
        closes=deque(maxlen=60);values=deque(maxlen=20)
        for day in calendar:
            raw=price.get(day)
            if raw is None or raw.close is None or raw.close<=0 or not raw.volume or raw.volume<=0 or raw.turnover is None or raw.turnover<=0:
                closes.clear();values.clear();continue
            closes.append(float(raw.close));values.append(float(raw.turnover))
            if len(closes)<60 or len(values)<20 or day not in regimes[market] or day.year<2022:continue
            i=indices[day]
            # Raw closes have no robust total-return adjustment; do not let a
            # known ex-right/dividend date masquerade as a trend or crash.
            k=bisect_right(actions,calendar[i-59])
            if k<len(actions) and actions[k]<=day:
                report['excluded_action_in_lookback']+=1
                continue
            cs=list(closes);returns=[100*(cs[i]/cs[i-1]-1) for i in range(40,60)]
            features={'prior_return_5':100*(cs[-1]/cs[-6]-1),'volatility_20':stdev(returns),
                      'log_turnover_20':log(mean(values))}
            outcomes={};windows={}
            for horizon in HORIZONS:
                if i+horizon>=len(calendar):outcomes[horizon]=None;windows[horizon]=(None,None);continue
                entry=calendar[i+1];exit=calendar[i+horizon];en=price.get(entry);ex=price.get(exit)
                windows[horizon]=(entry,exit)
                # The match is selected without this future information. Ex-post
                # missing-action outcomes are reported as attrition, not fills.
                k=bisect_right(actions,day);has_action=k<len(actions) and actions[k]<=exit
                if has_action or not en or not ex or not en.volume or not ex.volume or en.volume<=0 or ex.volume<=0:
                    outcomes[horizon]=None
                else:outcomes[horizon]=net_return(en.open,ex.close)
            prevday=calendar[i-1] if i else None
            record={'code':code,'day':day,'market':market,'features':features,'model':model[code].get(day),
                    'previous_model':model[code].get(prevday),'index':i,'outcomes':outcomes,'windows':windows,
                    'stock_trend':'bull' if mean(cs[-20:])>mean(cs) else 'nonbull','index_trend':regimes[market][day]}
            by_day[day].append(record);report['feature_stock_days']+=1
    return by_day,dict(report)

def date_snapshot(record,horizon):
    entry,exit=record['windows'][horizon]
    # This is an explicit scenario decision time, not evidence of source publication.
    decision=datetime.combine(record['day'],time(23,59),ZoneInfo('Asia/Taipei'))
    return Snapshot(code=record['code'],signal_date=record['day'],market=record['market'],industry=None,
        decision_at=decision,features_available_at=None,features=record['features'],
        outcome_pct=record['outcomes'][horizon],entry_date=entry,exit_date=exit)

def describe(matches):
    if not matches:return {'events':0,'signal_dates':0,'mean_excess_pp':None,'win_pct':None}
    excess=[m['matched_excess_pp'] for m in matches]
    return {'events':len(matches),'signal_dates':len({m['signal_date'] for m in matches}),
            'mean_excess_pp':mean(excess),'median_excess_pp':median(excess),
            'win_pct':100*sum(v>0 for v in excess)/len(excess)}

def run(by_day,calendar,draws):
    results=[];all_matches=[]
    for mode in ('strict','sensitivity_5pct'):
        for threshold in THRESHOLDS:
            for horizon in HORIZONS:
                signals=[];controls=[];trend_by_key={};next_eligible={}
                for day in sorted(by_day):
                    daily=by_day[day]
                    # Determine every crossing before non-overlap gating: an
                    # overlapping signal must not become a control.
                    cross={r['code'] for r in daily if crossing(r['model'],r['previous_model'],threshold,mode)}
                    for r in daily:
                        usable=(valid_model(r['model'],mode) and valid_model(r['previous_model'],mode)
                                and r['model']['segment']==r['previous_model']['segment'])
                        if not usable:continue
                        snap=date_snapshot(r,horizon)
                        if r['code'] in cross:
                            if r['index']>=next_eligible.get(r['code'],0):
                                signals.append(snap);next_eligible[r['code']]=r['index']+horizon
                                trend_by_key[(day,r['code'])]=r['stock_trend']+'_'+r['index_trend']
                        else:controls.append(snap)
                signal_days={r.signal_date for r in signals}
                controls=[r for r in controls if r.signal_date in signal_days]
                matched=match_on_date(signals,controls,CALIPERS,require_same_industry=False,allow_unverified_availability=True)
                for group in GROUPS:
                    subset=[x for x in matched['matches'] if group=='all' or trend_by_key[(x['signal_date'],x['signal_code'])]==group]
                    inf=moving_block_inference(subset,calendar,block_days=10,draws=draws)
                    periods={name:describe([x for x in subset if start<=x['signal_date'].year<=end])
                             for name,start,end in [('2022-2023',2022,2023),('2024',2024,2024),('2025-2026',2025,2026)]}
                    result={'quality':mode,'threshold':threshold,'holding_days':horizon,'trend':group,
                        'signal_count_before_matching':len(signals) if group=='all' else sum(v==group for v in trend_by_key.values()),
                        'matching_attrition_all_trends':matched['attrition'],'summary':describe(subset),'inference':inf,'periods_descriptive':periods}
                    results.append(result)
                    for m in subset:
                        if group=='all':all_matches.append({'quality':mode,'threshold':threshold,'holding_days':horizon,**m,'trend':trend_by_key[(m['signal_date'],m['signal_code'])]})
                print(mode,threshold,horizon,'signals',len(signals),'matched',len(matched['matches']),flush=True)
    pvalues=[r['inference'].get('p_value') for r in results]
    bh=adjust_pvalues(pvalues,method='BH',planned_tests=len(results));by=adjust_pvalues(pvalues,method='BY',planned_tests=len(results))
    for r,b,y in zip(results,bh,by):r['bh_q']=b;r['by_q']=y
    return results,all_matches

def main():
    p=argparse.ArgumentParser();p.add_argument('--draws',type=int,default=2000);args=p.parse_args()
    quality=json.loads(Path('research/factor_data/quality.json').read_text(encoding='utf8'))
    with Session() as db:
        calendar=sorted(load_calendar(db,date(2021,10,1),date(2026,11,3)))
        by_day,feature_report=make_feature_rows(calendar,quality,'research/factor_data/model_rows.jsonl.gz',db)
    results,matches=run(by_day,calendar,args.draws)
    report={'generated_at':datetime.now(timezone.utc),'protocol':'docs/FACTOR_PROTOCOL.md','study_start':'2022-01-01','study_end':'2026-11-03',
        'snapshot_stocks':quality['processed_stocks'],'source_exclusions':quality['exclusions'],'cost_breaks':quality['break_kinds'],
        'features':feature_report,'availability':'unverified historical source availability; no actual PIT timestamp was invented',
        'planned_tests':len(results),'tested':sum(r['inference']['status']=='ok' for r in results),
        'bh_rejections':sum(r['bh_q'] is not None and r['bh_q']<.05 for r in results),
        'by_rejections':sum(r['by_q'] is not None and r['by_q']<.05 for r in results),
        'limitations':['historical current-top-500 universe and current market classification create survivorship and lookahead bias',
            'event-dated outcomes are omitted ex post when reliable corporate-action returns are unavailable',
            'price and margin publication time cannot be independently verified; no genuine PIT or out-of-sample claim',
            'matching excludes historical sector, size and beta; missing/limit-up/down execution and capacity not modelled',
            'signal group, decision periods and thresholds were already examined; BY does not correct all prior research attempts',
            '5% sensitivity is a scenario, not a confidence interval or verified cost accuracy'],
        'results':results}
    Path('research/factor_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf8')
    with gzip.open('research/factor_matches.jsonl.gz','wt',encoding='utf8') as f:
        for row in matches:f.write(json.dumps(row,ensure_ascii=False,default=str,separators=(',',':'))+'\n')
    print(json.dumps({'planned_tests':report['planned_tests'],'tested':report['tested'],'bh_rejections':report['bh_rejections'],'by_rejections':report['by_rejections'],'matches':len(matches)}),flush=True)

if __name__=='__main__':main()
