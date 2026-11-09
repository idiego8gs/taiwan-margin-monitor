"""Paired market-return event study, with explicit restrictions and attrition.

Research only: excess return is NOT risk-adjusted alpha or a causal estimate.
"""
from __future__ import annotations
from collections import Counter
from datetime import date,datetime,timezone
from decimal import Decimal as D
from pathlib import Path
from statistics import mean,median
import json
from sqlalchemy import select
from .calculation import calculate,load_actions
from .db import Session,ProviderObservation
from .engine import DataQualityError

FEE=D('0.001425'); TAX=D('0.003')

def paired_events(code,series,prices,benchmark,event_dates,threshold,horizon,split):
    """Crossing t after close; next open entry, t+h close exit, no overlapping same-stock trades."""
    hits=[];excluded=Counter();next_eligible=0
    for i in range(1,len(series)):
        a,b=series[i-1],series[i]
        if a['raw_maintenance'] is None or b['raw_maintenance'] is None: continue
        if not (a['raw_maintenance']>threshold and b['raw_maintenance']<=threshold): continue
        if i<next_eligible:
            excluded['overlapping_same_stock']+=1;continue
        if i+horizon>=len(series):
            excluded['insufficient_future_days']+=1;continue
        entry=series[i+1]['date'];exit=series[i+horizon]['date']
        if any(b['date']<d<=exit for d in event_dates):
            excluded['corporate_action_in_holding_window']+=1;continue
        inp=prices.get(entry);out=prices.get(exit);bench_in=benchmark.get(entry);bench_out=benchmark.get(exit)
        if not inp or not out or inp.open is None or out.close is None or inp.open<=0 or out.close<=0:
            excluded['missing_stock_price']+=1;continue
        if not getattr(inp,'volume',None) or not getattr(out,'volume',None) or inp.volume<=0 or out.volume<=0:
            excluded['missing_or_zero_trading_volume']+=1;continue
        if not bench_in or not bench_out or bench_in['open']<=0 or bench_out['close']<=0:
            excluded['missing_benchmark']+=1;continue
        stock=(D(out.close)*(1-FEE-TAX)/(D(inp.open)*(1+FEE))-1)*100
        market=(bench_out['close']/bench_in['open']-1)*100
        hits.append({'code':code,'signal_date':b['date'],'entry_date':entry,'exit_date':exit,
                     'maintenance_pct':b['raw_maintenance'],'stock_net_pct':stock,
                     'index_gross_pct':market,'excess_pct':stock-market,
                     'period':('early' if exit<=split else 'late' if b['date']>split else 'crosses_split'),
                     'warmup':b['warmup']})
        # Exit is at the close, so a new signal at that same close can enter next session.
        next_eligible=i+horizon
    return hits,dict(excluded)

def summary(events):
    if not events:return {'n':0,'unique_stocks':0,'unique_signal_dates':0,'mean_net_pct':None,'mean_index_pct':None,'mean_excess_pp':None,'median_excess_pp':None,'excess_positive_pct':None}
    excess=[x['excess_pct'] for x in events]
    return {'n':len(events),'unique_stocks':len({x['code'] for x in events}),
            'unique_signal_dates':len({x['signal_date'] for x in events}),
            'mean_net_pct':mean(x['stock_net_pct'] for x in events),
            'mean_index_pct':mean(x['index_gross_pct'] for x in events),
            'mean_excess_pp':mean(excess),'median_excess_pp':median(excess),
            'excess_positive_pct':D(sum(x>0 for x in excess))/len(excess)*100}

def run(db,universe,benchmark,start,end,split,tpex_benchmark=None,thresholds=(130,140,150),horizons=(5,10)):
    if not start<split<end:raise ValueError('invalid chronological split')
    cache={};excluded=Counter();detail={}
    for stock in universe['items']:
        code=stock['code']
        market=stock['market']
        if market=='TPEx' and tpex_benchmark is None:
            excluded['tpex_without_matched_benchmark']+=1;continue
        if market not in ('TWSE','TPEx'):
            excluded['unknown_market']+=1;continue
        try:
            # Four categories must cover the whole study window; still not exhaustive PIT coverage.
            actions,warnings=load_actions(db,code,start,end)
            if 'corporate_action_data_incomplete' in warnings:
                excluded['corporate_action_history_incomplete']+=1;continue
            est=calculate(db,code,start_at=start,as_of=end,buy_price_method='close')
            if not est:excluded['no_data']+=1;continue
            if not est['calendar_verified_against_finmind']:
                excluded['trading_calendar_missing']+=1;continue
            if est['source']!='FinMind' or est['first_observation']!=start:
                excluded['inadequate_history']+=1;continue
            if len(est['data'])<2:
                excluded['insufficient_model_history']+=1;continue
            rows=db.scalars(select(ProviderObservation).where(ProviderObservation.provider=='FinMind',
                ProviderObservation.code==code,ProviderObservation.trading_date>=start,
                ProviderObservation.trading_date<=end)).all()
            prices={r.trading_date:r for r in rows}; cache[code]=(est['data'],prices,set(actions),market)
            detail[code]={'days':len(est['data']),'blocked':est['blocked'],'warnings':est['quality_flags']}
        except (DataQualityError,ValueError) as exc:
            excluded['invalid_input']+=1;detail[code]={'error':str(exc)}
    output=[]
    for threshold in thresholds:
        for horizon in horizons:
            all_events=[];drops=Counter()
            for code,(series,prices,actions,market) in cache.items():
                market_benchmark=benchmark if market=='TWSE' else tpex_benchmark
                events,skips=paired_events(code,series,prices,market_benchmark,actions,D(threshold),horizon,split)
                for event in events: event['market']=market
                all_events.extend(events);drops.update(skips)
            output.append({'threshold':threshold,'holding_days':horizon,'all':summary(all_events),
                'early':summary([x for x in all_events if x['period']=='early']),
                'late':summary([x for x in all_events if x['period']=='late']),
                'twse':summary([x for x in all_events if x['market']=='TWSE']),
                'tpex':summary([x for x in all_events if x['market']=='TPEx']),
                'excluded_signals':dict(drops),'events':all_events})
    return {'start':start,'end':end,'split':split,'generated_at':datetime.now(timezone.utc),
        'universe_selected':len(universe['items']),'eligible_stocks':len(cache),
        'stock_exclusions':dict(excluded),'stocks':detail,'results':output,
        'stock_source':'FinMind TaiwanStockPrice and TaiwanStockMarginPurchaseShortSale',
        'benchmark_source':'TWSE TAIEX and TPEx price indices, separately matched by current market classification',
        'method':'收盤後跌破門檻，次日開盤買進、第 h 個交易日收盤賣出；分別以上市、上櫃同期間價格指數開盤至收盤為參考；股權報酬扣買賣手續費各 0.1425% 與賣出證交稅 0.3%，指數不扣費用。',
        'limitations':['超額報酬不是風險調整 Alpha，指數不可直接交易且未扣指數交易成本',
            '非 point-in-time 資料，歷史行情與交易日曆可能事後修訂',
            '以當前市值前 500 名為樣本，存在嚴重倖存者與選樣偏差',
            '公司行動四類資料齊全仍非所有事件完整；已知事件持有期間排除',
            '成本暖機、固定成數與截斷起點事後選定，門檻及時間切分非事前註冊，並非真正樣本外；跨切分日事件排除於分段統計',
            '未處理漲跌停或流動性對實際成交的影響、滑價、未成交及下市',
            '跨股票同日事件並不獨立；多門檻測試未做多重比較校正，不得宣稱顯著優勢']}

def parse_benchmark(raw):
    """Accept dated OHLC rows or a date-to-OHLC map."""
    rows=raw.get('rows',raw.get('data',[]));out={}
    if 'days' in raw: rows=[{'date':day,**values} for day,values in raw['days'].items()]
    for x in rows:
        if not isinstance(x,dict):raise ValueError('unsupported benchmark row')
        day=date.fromisoformat(x['date']);o=D(str(x['open']));c=D(str(x['close']))
        if day in out or not o.is_finite() or not c.is_finite() or o<=0 or c<=0:raise ValueError('invalid benchmark')
        out[day]={'open':o,'close':c}
    if not out:raise ValueError('empty benchmark')
    return out

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--universe',default='research/top500_20261103/universe.json');p.add_argument('--benchmark',required=True);p.add_argument('--tpex-benchmark')
    p.add_argument('--output',required=True);p.add_argument('--start',default='2026-07-08');p.add_argument('--end',default='2026-11-03');p.add_argument('--split',default='2026-08-31');a=p.parse_args()
    u=json.loads(Path(a.universe).read_text(encoding='utf8'));benchmark=parse_benchmark(json.loads(Path(a.benchmark).read_text(encoding='utf8')))
    tpex_benchmark=parse_benchmark(json.loads(Path(a.tpex_benchmark).read_text(encoding='utf8'))) if a.tpex_benchmark else None
    with Session() as db: result=run(db,u,benchmark,date.fromisoformat(a.start),date.fromisoformat(a.end),date.fromisoformat(a.split),tpex_benchmark)
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf8')
    print(json.dumps({'eligible_stocks':result['eligible_stocks'],'stock_exclusions':result['stock_exclusions']},ensure_ascii=False))
