"""Read-only, reproducible coverage audit for the 2026 top-500 research snapshot.

An absent observation is a gap, not proof that trading or margin was permitted.
This deliberately does not modify or backfill the observations table.
"""
from datetime import datetime,timezone
from pathlib import Path
import json
from sqlalchemy import text
from .db import engine
from .finmind import EVENTS
from .bulk_backfill import atomic_json

ROOT=Path('research/top500_20261103')
START='2021-10-01'; END='2026-11-03'

def classify(identity):
    """Identity contains yesterday,buy,sell,cash repayment,today in lots."""
    if any(x is None for x in identity):return 'missing'
    previous,buy,sell,cash,balance=identity
    return 'ok' if previous-sell-cash>=0 and previous+buy-sell-cash==balance else 'mismatch'

def build():
    universe=json.loads((ROOT/'universe.json').read_text(encoding='utf8'))
    symbols={x['code']:x for x in universe['items']}
    calendars={m:sorted(json.loads(Path(p).read_text(encoding='utf8'))['days']) for m,p in (
        ('TWSE','research/benchmark/taiex_202110_202611.json'),('TPEx','research/benchmark/tpex_202110_202611.json'))}
    details={code:{'code':code,'name':s['name'],'market':s['market'],'rows':0,'price_missing_rows':0,
        'margin_missing_rows':0,'balance_identity_mismatch_rows':0,'balance_identity_examples':[],
        'observed_dates':set(),'first_observed':None,'last_observed':None,'at_start_both':False,
        'at_end_both':False,'event_datasets':set()} for code,s in symbols.items()}
    with engine.connect() as db:
        q=text('''SELECT code,trading_date,close,previous_balance,margin_buy,margin_sell,repayment,balance
                  FROM provider_observations WHERE provider='FinMind' AND trading_date>=:start AND trading_date<=:end
                  ORDER BY code,trading_date''')
        for code,day,close,previous,buy,sell,cash,balance in db.execute(q,{'start':START,'end':END}):
            if code not in details:continue
            x=details[code];day=str(day);x['rows']+=1;x['observed_dates'].add(day)
            x['first_observed']=x['first_observed'] or day;x['last_observed']=day
            if close is None:x['price_missing_rows']+=1
            ident=classify((previous,buy,sell,cash,balance))
            if ident=='missing':x['margin_missing_rows']+=1
            if ident=='mismatch':
                x['balance_identity_mismatch_rows']+=1
                if len(x['balance_identity_examples'])<3:x['balance_identity_examples'].append(day)
            if day==START:x['at_start_both']=close is not None and ident!='missing'
            if day==END:x['at_end_both']=close is not None and ident!='missing'
        q=text('''SELECT DISTINCT code,dataset FROM provider_responses
                  WHERE provider='FinMind' AND http_status=200 AND start_date<=:start AND end_date>=:end''')
        for code,dataset in db.execute(q,{'start':START,'end':END}):
            if code in details and dataset in EVENTS:details[code]['event_datasets'].add(dataset)
    for x in details.values():
        days=x.pop('observed_dates');first=x['first_observed'];last=x['last_observed']
        # Limit to the observed interval: pre-listing and post-delisting are not assumed gaps.
        missing=[day for day in calendars[x['market']] if first and first<=day<=last and day not in days]
        x['unobserved_market_days_within_observed_span']=len(missing)
        x['unobserved_market_day_examples']=missing[:5]
        x['event_datasets']=sorted(x['event_datasets'])
        x['all_four_event_responses_archived']=len(x['event_datasets'])==len(EVENTS)
    values=list(details.values())
    summary={'selected_stocks':len(values),'stocks_with_any_observation':sum(x['rows']>0 for x in values),
      'stocks_with_both_price_and_margin_on_start':sum(x['at_start_both'] for x in values),
      'stocks_with_both_price_and_margin_on_end':sum(x['at_end_both'] for x in values),
      'stocks_with_price_missing_in_observed_rows':sum(x['price_missing_rows']>0 for x in values),
      'stocks_with_margin_missing_in_observed_rows':sum(x['margin_missing_rows']>0 for x in values),
      'stocks_with_no_margin_values':sum(x['rows']>0 and x['margin_missing_rows']==x['rows'] for x in values),
      'stocks_with_balance_identity_mismatch':sum(x['balance_identity_mismatch_rows']>0 for x in values),
      'balance_identity_mismatch_rows':sum(x['balance_identity_mismatch_rows'] for x in values),
      'stocks_with_unobserved_market_days':sum(x['unobserved_market_days_within_observed_span']>0 for x in values),
      'stocks_with_four_event_responses_archived':sum(x['all_four_event_responses_archived'] for x in values),
      'calendar_days':{m:len(c) for m,c in calendars.items()}}
    return {'created_at':datetime.now(timezone.utc).isoformat(),'from':START,'through':END,'summary':summary,
      'notes':['500 檔依 2026 年近似市值選取，無歷史成分與下市股，具有存活者偏差',
       '四類公司行動原始回應存在不保證內容完整、已處理或當時可得',
       '缺少觀察日期可能是停牌、掛牌狀態、交易市場轉換或資料缺漏；不能直接視為可交易',
       '歷史比較使用目前市場的指數日曆，未能證明歷史市場別',
       '融資餘額不守恆可能涉及公司行動或資料錯誤，必須逐筆查核，不可自行填值'],
      'stocks':values}

if __name__=='__main__':
    report=build();path=ROOT/'coverage_audit.json';atomic_json(path,report)
    print(json.dumps(report['summary'],ensure_ascii=False))
