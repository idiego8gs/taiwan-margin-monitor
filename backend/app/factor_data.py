"""Rebuild exploratory cost histories while preserving breaks and provenance.

The provider history is a retrospective snapshot, NOT a point-in-time source.
Unexplained discontinuities restart the model, never inventing missing trades.
"""
from __future__ import annotations
from collections import Counter
from datetime import date,datetime,timezone
from pathlib import Path
import argparse,gzip,json
from sqlalchemy import select
from .calculation import build_series,load_actions,load_calendar
from .db import Session,ProviderObservation
from .engine import DataQualityError

def segmented_series(rows, events, calendar, *, keep_cash_out_of_principal=False):
    """Break on unverified input; only restart from information then available.

    Events without interpretable terms and malformed days are skipped, not
    treated as valid. The next valid day starts a fresh warm-up. A change in
    previous_balance is not assumed to be a buy or sell.
    """
    if keep_cash_out_of_principal:
        from dataclasses import replace
        from decimal import Decimal
        # Cash dividends are kept as corporate-action facts, not repayments.
        events = {
            day: replace(event, action=replace(event.action, cash_distribution=Decimal(0)))
            if event.kind == 'cash_dividend' and event.action else event
            for day, event in events.items()
        }
    items=[];breaks=[];position=0;segment=0
    indices={r.trading_date:i for i,r in enumerate(rows)}
    while position<len(rows):
        result=build_series(rows[position:],events=events,calendar=calendar,buy_price_method='close')
        segment+=1
        for item in result['items']:
            item['segment']=segment
            items.append(item)
        failure=result['blocked']
        if not failure:break
        bad=indices[failure['date']]
        if bad<position:raise ValueError('non-progressing cost segment')
        kind=failure['kind']
        retry=kind in ('previous_balance_conflict','missing_trading_dates') and bad>position
        breaks.append({**failure,'restart_policy':'restart_at_reported_day' if retry else 'skip_unusable_day'})
        position=bad if retry else bad+1
    return items,breaks

def main():
    p=argparse.ArgumentParser();p.add_argument('--start',default='2021-10-01');p.add_argument('--end',default='2026-11-03');p.add_argument('--output',default='research/factor_data');a=p.parse_args()
    start=date.fromisoformat(a.start);end=date.fromisoformat(a.end);out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    universe=json.loads(Path('research/top500_20261103/universe.json').read_text(encoding='utf8'))
    exclusions=Counter();break_kinds=Counter();details={};rows_written=0;processed=0
    with Session() as db, gzip.open(out/'model_rows.jsonl.gz','wt',encoding='utf8') as f:
        calendar=load_calendar(db,start,end)
        if not calendar:raise RuntimeError('verified archived calendar absent for study window')
        for stock in universe['items']:
            code=stock['code'];
            rows=db.scalars(select(ProviderObservation).where(ProviderObservation.provider=='FinMind',ProviderObservation.code==code,ProviderObservation.trading_date>=start,ProviderObservation.trading_date<=end).order_by(ProviderObservation.trading_date)).all()
            if not rows:exclusions['no_price_margin_history']+=1;continue
            try:
                events,warnings=load_actions(db,code,start,end)
                if 'corporate_action_data_incomplete' in warnings:
                    exclusions['missing_full_action_sources']+=1;continue
                results,breaks=segmented_series(rows,events,calendar)
            except (DataQualityError,ValueError) as exc:
                exclusions['invalid_input']+=1;details[code]={'error':str(exc)};continue
            by_day={r.trading_date:r for r in rows}
            for x in results:
                source=by_day[x['date']]
                record={'code':code,'market_current':stock['market'],'date':x['date'],'segment':x['segment'],
                    'open':source.open,'close':source.close,'volume':source.volume,'turnover':source.turnover,
                    'maintenance':x['raw_maintenance'],'balance':x['balance'],'warmup':x['warmup'],
                    'sensitivity_pct':x['initial_cost_sensitivity_pct']}
                f.write(json.dumps(record,ensure_ascii=False,default=str,separators=(',',':'))+'\n');rows_written+=1
            for failure in breaks:break_kinds[failure['kind']]+=1
            details[code]={'rows':len(rows),'valid_model_days':len(results),'breaks':len(breaks),
                'break_details':breaks,'action_dates':sorted(events),'warnings':warnings}
            processed+=1
            if processed%50==0:print('processed',processed,'model_days',rows_written,flush=True)
    report={'generated_at':datetime.now(timezone.utc),'start':start,'end':end,'universe_selected':len(universe['items']),
        'processed_stocks':processed,'exclusions':dict(exclusions),'break_kinds':dict(break_kinds),
        'model_days':rows_written,'details':details,
        'method':'遇餘額斷點或缺漏停止當段並標記，未解釋調整不假裝為融資買賣；下一可用日重新初始化，持續標記暖機和原始成本敏感度。',
        'limitations':['所有回溯資料不是 point-in-time','上市櫃與樣本採現時名單，有存活者與市場轉換偏差','公司行動來源未涵蓋所有類型，且事件公告時點未完整保存','暖機敏感度不是估計成本的可信區間','報表公開時間尚未逐筆驗證，不可宣稱訊號可在指定時點交易']}
    (out/'quality.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf8')
    print(json.dumps({k:report[k] for k in ('processed_stocks','exclusions','break_kinds','model_days')},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
