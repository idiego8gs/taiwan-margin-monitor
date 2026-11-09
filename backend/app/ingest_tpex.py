"""Historical TPEx margin and round-lot quote reports, kept separate from FinMind."""
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import argparse
import json
import logging
import time
from urllib.parse import urlencode
from sqlalchemy import select
from .db import Base, engine, Session, Observation, RawResponse
from .ingest import fetch, number

log=logging.getLogger(__name__)
MARGIN_URL='https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php'
PRICE_URL='https://www.tpex.org.tw/www/zh-tw/afterTrading/otc'

def urls(day):
    roc=f'{day.year-1911:03d}/{day.month:02d}/{day.day:02d}'
    return {'margin':MARGIN_URL+'?'+urlencode({'l':'zh-tw','d':roc}),
            'quotes':PRICE_URL+'?'+urlencode({'date':day.strftime('%Y/%m/%d'),'type':'EW','response':'json'})}

def field_index(table, required):
    fields=[str(x).strip() for x in table.get('fields',[])]
    if len(fields)!=len(set(fields)) or any(x not in fields for x in required):
        raise ValueError('unexpected TPEx report schema')
    return {x:fields.index(x) for x in required}

def update(day, from_directory=None, session_factory=Session):
    source_urls=urls(day); responses={}
    for key,url in source_urls.items():
        if from_directory:
            raw=Path(from_directory,f'{key}-{day:%Y%m%d}.json').read_text(encoding='utf-8-sig')
            responses[key]=(raw,json.loads(raw))
        else:
            responses[key]=fetch(url)
    # Archive exactly what was returned before validating and transforming it.
    with session_factory.begin() as db:
        for key,(raw,_) in responses.items():
            db.add(RawResponse(source=source_urls[key],trading_date=day,payload=raw))
    margin=responses['margin'][1]; quotes=responses['quotes'][1]
    if margin.get('date') != day.strftime('%Y%m%d'):
        raise ValueError('TPEx margin response date does not match request')
    mtables=margin.get('tables',[]); qtables=quotes.get('tables',[])
    if len(mtables)!=1 or len(qtables)!=1:
        raise ValueError('TPEx reports missing or ambiguous tables')
    m=mtables[0]; q=qtables[0]
    if q.get('date') != f'{day.year-1911:03d}/{day.month:02d}/{day.day:02d}':
        raise ValueError('TPEx quote response date does not match request')
    mfields=field_index(m,['代號','名稱','前資餘額(張)','資買','資賣','現償','資餘額','資限額'])
    qfields=field_index(q,['代號','開盤','最高','最低','收盤','成交股數','成交金額(元)'])
    if not m.get('data') or not q.get('data'):
        raise ValueError('TPEx report has no rows')
    quote_map={r[qfields['代號']]:r for r in q['data']}
    count=0; skipped=0
    with session_factory.begin() as db:
        for r in m['data']:
            try:
                code=str(r[mfields['代號']]); name=str(r[mfields['名稱']])
                vals=[number(r[mfields[x]]) for x in ['前資餘額(張)','資買','資賣','現償','資餘額','資限額']]
                if any(x is None or x<0 or x!=int(x) for x in vals):
                    skipped+=1;continue
                prev,buy,sell,repay,balance,limit=map(int,vals)
                qr=quote_map.get(code)
                def qnum(field): return number(qr[qfields[field]]) if qr else None
                observation=db.scalar(select(Observation).where(Observation.market=='TPEx',
                     Observation.code==code,Observation.trading_date==day))
                if observation is None:
                    observation=Observation(market='TPEx',code=code,trading_date=day);db.add(observation)
                observation.name=name
                observation.open=qnum('開盤');observation.high=qnum('最高');observation.low=qnum('最低');observation.close=qnum('收盤')
                volume=qnum('成交股數');observation.volume=int(volume) if volume is not None else None
                observation.turnover=qnum('成交金額(元)')
                observation.previous_balance=prev;observation.margin_buy=buy;observation.margin_sell=sell
                observation.repayment=repay;observation.balance=balance;observation.limit=limit
                observation.quality='balance_mismatch' if prev+buy-sell-repay!=balance else 'unreviewed_corporate_actions'
                count+=1
            except (IndexError,TypeError):
                # A changed row shape must roll back the entire day.
                raise ValueError('unexpected TPEx row shape') from None
    return {'upserted':count,'skipped_invalid':skipped,'quotes_basis':'round_lot_only'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--start',required=True);p.add_argument('--end',required=True)
    p.add_argument('--from-directory',type=Path)
    args=p.parse_args();first=date.fromisoformat(args.start);last=date.fromisoformat(args.end)
    if last<first: p.error('end must be on or after start')
    Base.metadata.create_all(engine)
    failures=0
    while first<=last:
        if first.weekday()<5:
            try: print(first,update(first,args.from_directory),flush=True)
            except Exception:
                failures+=1
                log.exception('TPEx import failed for %s',first)
            time.sleep(1)
        first+=timedelta(days=1)
    if failures:
        raise SystemExit(1)
if __name__=='__main__': main()
