"""Exploratory close-signal event study; never a production trading recommendation."""
from datetime import date
from decimal import Decimal as D
from statistics import mean, median
from sqlalchemy import select
from .calculation import calculate
from .db import Session, ProviderObservation, Observation

FEE=D('0.001425')
TAX=D('0.003')


def events_for_series(code, series, prices, action_days, threshold, horizon):
    """Cross below threshold at close t; enter next open, exit at close t+h."""
    events=[]; controls=[]
    for i in range(1,len(series)-horizon):
        prev,cur=series[i-1],series[i]
        if prev['raw_maintenance'] is None or cur['raw_maintenance'] is None:
            continue
        signal_date=cur['date']; entry_date=series[i+1]['date']; exit_date=series[i+horizon]['date']
        if any(signal_date < d <= exit_date for d in action_days):
            continue
        entry=prices.get(entry_date); exit=prices.get(exit_date)
        if not entry or not exit or entry.open is None or exit.close is None or entry.open<=0 or exit.close<=0:
            continue
        gross=(D(exit.close)/D(entry.open)-1)*100
        net=(D(exit.close)*(1-FEE-TAX)/(D(entry.open)*(1+FEE))-1)*100
        row={'code':code,'signal_date':signal_date,'entry_date':entry_date,'exit_date':exit_date,
             'maintenance':cur['raw_maintenance'],'gross_return_pct':gross,'net_return_pct':net}
        controls.append(row)
        if prev['raw_maintenance']>threshold and cur['raw_maintenance']<=threshold:
            events.append(row)
    return events,controls


def summarize(rows):
    vals=[x['net_return_pct'] for x in rows]
    return {'n':len(vals),'mean_net_pct':mean(vals) if vals else None,
            'median_net_pct':median(vals) if vals else None,
            'win_rate_pct':D(sum(v>0 for v in vals))/len(vals)*100 if vals else None}


def run(db,codes,start,end,thresholds=(130,140,150),horizons=(5,10)):
    results=[]; quality={}; cache={}
    for code in codes:
        estimate=calculate(db,code,start_at=start,as_of=end,buy_price_method='close')
        if not estimate: quality[code]={'error':'missing'}; continue
        model=ProviderObservation if estimate['source']=='FinMind' else Observation
        q=select(model).where(model.code==code,model.trading_date>=start,model.trading_date<=end)
        if model is ProviderObservation: q=q.where(model.provider=='FinMind')
        prices={p.trading_date:p for p in db.scalars(q).all()}
        series=estimate['data'];actions={a['date'] for a in estimate['actions']}
        quality[code]={'days':len(series),'blocked':estimate['blocked'],'flags':estimate['quality_flags']}
        cache[code]=(series,prices,actions)
    for threshold in thresholds:
        for horizon in horizons:
            hits=[]; all_controls=[]
            for code,(series,prices,actions) in cache.items():
                event,control=events_for_series(code,series,prices,actions,D(threshold),horizon)
                hits.extend(event); all_controls.extend(control)
            results.append({'threshold':threshold,'holding_days':horizon,'events':summarize(hits),
                            'comparison_all_stock_days':summarize(all_controls),'event_details':hits})
    return {'start':start,'end':end,'codes':codes,'results':results,'quality':quality,
            'limitations':['僅探索指定股票，不是市值前500大或代表性樣本','歷史資料及日曆可能事後修訂，非 point-in-time',
                           '暖機起點與融資成數為假設，未校準','未處理所有公司行動或漲跌停無法成交',
                           '事件視窗可重疊，控制組為同期間全部可用股日，非因果比較',
                           '忽略滑價，費率假設買賣各0.1425%、賣出稅0.3%；實際費率可能不同']}


if __name__=='__main__':
    import argparse,json
    from .main import decimal_strings
    p=argparse.ArgumentParser();p.add_argument('--start',default='2026-07-08');p.add_argument('--end',default='2026-11-03')
    p.add_argument('--codes',default='3363,2308,8039,2454');p.add_argument('--output')
    a=p.parse_args()
    with Session() as db: result=run(db,a.codes.split(','),date.fromisoformat(a.start),date.fromisoformat(a.end))
    output=json.dumps(decimal_strings(result),ensure_ascii=False,indent=2,default=str)
    if a.output:
        from pathlib import Path
        Path(a.output).write_text(output,encoding='utf-8')
    else: print(output)
