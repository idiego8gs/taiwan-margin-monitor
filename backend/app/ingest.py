import argparse, json, logging, time, os
from pathlib import Path
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.request import Request, urlopen
from sqlalchemy import select
from .db import Base, engine, Session, Observation, RawResponse

log = logging.getLogger(__name__)
URLS = {
 'margin': 'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={date}&selectType=ALL&response=json',
 'quotes': 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date}&type=ALLBUT0999&response=json',
}
def fetch(url):
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent':'TaiwanMarginMonitor/0.1 contact: local-research'}), timeout=30) as response:
                data=response.read().decode('utf-8-sig')
            return data, json.loads(data)
        except Exception:
            if attempt == 2: raise
            time.sleep(2 ** attempt)

def number(x):
    try: return Decimal(str(x).replace(',', '').strip())
    except (InvalidOperation, ValueError): return None

def table(obj, required):
    matches=[x for x in obj.get('tables',[]) if all(f in x.get('fields',[]) for f in required)]
    if len(matches)!=1: raise ValueError('missing or ambiguous source table: '+str(required))
    return matches[0]['data']

def update(day):
    fixture_dir=os.getenv('LOCAL_RESPONSE_DIR')
    if fixture_dir:
        responses={}
        for k in URLS:
            raw=Path(fixture_dir, f'{k}-{day:%Y%m%d}.json').read_text(encoding='utf-8-sig')
            responses[k]=(raw,json.loads(raw))
    else:
        responses={k:fetch(v.format(date=day.strftime('%Y%m%d'))) for k,v in URLS.items()}
    with Session.begin() as db:
        for k,(raw,obj) in responses.items(): db.add(RawResponse(source=URLS[k].format(date=day.strftime('%Y%m%d')),trading_date=day,payload=raw))
    objs={k:v[1] for k,v in responses.items()}
    if any(o.get('stat')!='OK' or o.get('date')!=day.strftime('%Y%m%d') for o in objs.values()):
        log.warning('no verified TWSE report on %s',day); return 0
    margins=table(objs['margin'],['代號','現金償還','次一營業日限額'])
    quotes=table(objs['quotes'],['證券代號','成交股數','成交金額','開盤價','收盤價'])
    quote_map={x[0]:x for x in quotes}
    count=0
    with Session.begin() as db:
        for m in margins:
            if len(m)<8: raise ValueError('unexpected margin row')
            vals=[number(x) for x in m[2:8]]
            if any(x is None or x != int(x) or x<0 for x in vals): continue
            buy,sell,repay,prev,balance,limit=map(int,vals)
            q=quote_map.get(m[0]); prices=[number(q[i]) for i in [5,6,7,8]] if q else [None]*4
            existing=db.scalar(select(Observation).where(Observation.market=='TWSE',Observation.code==m[0],Observation.trading_date==day))
            if existing is None:
                existing=Observation(market='TWSE',code=m[0],trading_date=day); db.add(existing)
            existing.name=m[1]; existing.open,existing.high,existing.low,existing.close=prices
            existing.volume=int(number(q[2])) if q and number(q[2]) is not None else None
            existing.turnover=number(q[4]) if q else None
            existing.margin_buy=buy; existing.margin_sell=sell; existing.repayment=repay
            existing.previous_balance=prev; existing.balance=balance; existing.limit=limit
            existing.quality='balance_mismatch' if prev+buy-sell-repay!=balance else 'unreviewed_corporate_actions'
            count+=1
    return count

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--start',required=True); parser.add_argument('--end',required=True); args=parser.parse_args()
    Base.metadata.create_all(engine)
    day=date.fromisoformat(args.start); end=date.fromisoformat(args.end)
    while day<=end:
        if day.weekday()<5:
            try: print(day,update(day),flush=True)
            except Exception: log.exception('failed %s; retry this date later',day)
            time.sleep(1)
        day+=timedelta(days=1)
if __name__=='__main__': main()
