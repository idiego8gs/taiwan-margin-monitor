"""Archive and validate TWSE daily TAIEX open/close benchmarks."""
from datetime import date,datetime,timezone
from decimal import Decimal,InvalidOperation
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,urlopen
import hashlib,json,time

URL='https://www.twse.com.tw/indicesReport/MI_5MINS_HIST'
FIELDS=['日期','開盤指數','最高指數','最低指數','收盤指數']
TPEX_URL='https://www.tpex.org.tw/www/zh-tw/indexInfo/inx'

def parse_report(raw,year,month):
    obj=json.loads(raw,parse_float=Decimal)
    if obj.get('stat')!='OK' or obj.get('fields')!=FIELDS or not isinstance(obj.get('data'),list):
        raise ValueError('unexpected TWSE index schema')
    values={}
    for r in obj['data']:
        if len(r)!=5:raise ValueError('unexpected TWSE index row')
        try:
            y,m,d=map(int,r[0].split('/')); day=date(y+1911,m,d)
            prices=[Decimal(str(x).replace(',','')) for x in r[1:]]
        except (ValueError,TypeError,InvalidOperation):raise ValueError('invalid TWSE index data') from None
        if day.year!=year or day.month!=month or day in values or any(not p.is_finite() or p<=0 for p in prices):
            raise ValueError('invalid TWSE index date or price')
        op,hi,lo,close=prices
        if lo>min(op,close) or hi<max(op,close):raise ValueError('inconsistent TWSE index OHLC')
        values[day.isoformat()]={'open':str(op),'high':str(hi),'low':str(lo),'close':str(close)}
    return values

def parse_tpex_report(raw,year,month):
    obj=json.loads(raw,parse_float=Decimal)
    tables=obj.get('tables',[])
    if obj.get('stat')!='ok' or len(tables)!=1 or tables[0].get('fields')!=['日期','開市','最高','最低','收市','漲/跌']:
        raise ValueError('unexpected TPEx index schema')
    if tables[0].get('date')!=f'{year-1911:03d}/{month:02d}':raise ValueError('TPEx index month mismatch')
    values={}
    for r in tables[0]['data']:
        if len(r)!=6:raise ValueError('unexpected TPEx index row')
        try:
            day=date.fromisoformat(r[0].replace('/','-'));prices=[Decimal(str(x).replace(',','')) for x in r[1:5]]
        except (ValueError,InvalidOperation,TypeError):raise ValueError('invalid TPEx index data') from None
        if day.year!=year or day.month!=month or day in values or any(not p.is_finite() or p<=0 for p in prices):raise ValueError('invalid TPEx index date or price')
        op,hi,lo,close=prices
        if lo>min(op,close) or hi<max(op,close):raise ValueError('inconsistent TPEx index OHLC')
        values[day.isoformat()]={'open':str(op),'high':str(hi),'low':str(lo),'close':str(close)}
    return values

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--start',default='2026-07-01');p.add_argument('--end',default='2026-11-03');p.add_argument('--output',default='research/benchmark');p.add_argument('--market',choices=['TWSE','TPEx'],default='TWSE');a=p.parse_args()
    start=date.fromisoformat(a.start);end=date.fromisoformat(a.end)
    if end<start:raise ValueError('end precedes start')
    directory=Path(a.output);directory.mkdir(parents=True,exist_ok=True)
    month=date(start.year,start.month,1);out={};sources=[]
    while month<=end:
        endpoint=URL if a.market=='TWSE' else TPEX_URL
        query_date=month.strftime('%Y%m%d') if a.market=='TWSE' else f'{month.year-1911:03d}/{month.month:02d}'
        url=endpoint+'?'+urlencode({'date':query_date,'response':'json'})
        with urlopen(Request(url,headers={'User-Agent':'TaiwanMarginMonitor/1.0 research'}),timeout=30) as response:raw=response.read()
        text=raw.decode('utf-8-sig');parsed=(parse_report if a.market=='TWSE' else parse_tpex_report)(text,month.year,month.month)
        (directory/f'{a.market.lower()}_index_{month:%Y%m}.json').write_bytes(raw)
        sources.append({'url':url,'fetched_at':datetime.now(timezone.utc).isoformat(),'sha256':hashlib.sha256(raw).hexdigest()})
        out.update({k:v for k,v in parsed.items() if start.isoformat()<=k<=end.isoformat()})
        month=date(month.year+(month.month==12),month.month%12+1,1);time.sleep(1)
    result={'source':('TWSE TAIEX' if a.market=='TWSE' else 'TPEx index')+' price index, not total return','start':str(start),'end':str(end),'sources':sources,'days':out}
    name='taiex' if a.market=='TWSE' else 'tpex';file=directory/f'{name}_{start:%Y%m}_{end:%Y%m}.json';file.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'days':len(out),'path':str(file),'first':min(out) if out else None,'last':max(out) if out else None}))
if __name__=='__main__':main()
