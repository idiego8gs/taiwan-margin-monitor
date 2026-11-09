"""Compare the pictured projects without executing an external strategy repository."""
from __future__ import annotations
import argparse
import csv
from datetime import date
import io
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from .finmind import PRICE, MARGIN, fetch_raw, parse_response, request_url, ProviderError

def probe_finmind(code, day):
    result={}
    for dataset in (PRICE,MARGIN):
        url=request_url(dataset,code,day,day)
        raw,status,_=fetch_raw(url,os.environ.get('FINMIND_TOKEN'))
        result[dataset]=parse_response(raw,status,dataset,code,day,day).get(day)
    return result

def probe_twstock(code,day):
    try:
        import twstock
    except ImportError:
        return {'error':'install backend/requirements-research.txt for the optional twstock probe'}
    try:
        stock=twstock.Stock(code, initial_fetch=False)
        rows=stock.fetch(day.year,day.month)
        row=next((r for r in rows if r.date.date()==day),None)
        if row is None: return {'available':False}
        return {'available':True,'open':row.open,'high':row.high,'low':row.low,'close':row.close,
                'volume':row.capacity,'turnover':row.turnover,
                'volume_note':'twstock TPEx historical monthly report converts thousand-share and thousand-NTD fields'}
    except Exception as exc:
        return {'error':f'{type(exc).__name__}: {exc}'}

def probe_voidful(code,day):
    # request_url validates the code, preventing arbitrary URL construction.
    request_url(PRICE,code,day,day)
    url=f'https://raw.githubusercontent.com/voidful/tw_stocker/main/data/{code}.csv'
    try:
        with urlopen(Request(url,headers={'User-Agent':'TaiwanMarginMonitor/0.1'}),timeout=45) as response:
            payload=response.read(20_000_001)
        if len(payload)>20_000_000:
            return {'url':url,'error':'CSV exceeds the 20 MB research probe limit'}
        reader=csv.DictReader(io.StringIO(payload.decode('utf-8-sig')))
        fields=reader.fieldnames or []
        date_field='Datetime' if 'Datetime' in fields else 'Date' if 'Date' in fields else None
        if not date_field: return {'url':url,'error':'unrecognised date field','fields':fields}
        matches=[r for r in reader if str(r.get(date_field,'')).startswith(day.isoformat())]
        return {'url':url,'available':True,'fields':fields,'rows_on_date':len(matches),
                'last_row':matches[-1] if matches else None,
                'note':'Rows are reported as supplied; intraday CSV is not treated as a verified daily series.'}
    except HTTPError as exc:
        return {'url':url,'available':False,'http_status':exc.code}
    except (URLError,TimeoutError,UnicodeError) as exc:
        return {'url':url,'error':type(exc).__name__}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stock',required=True);parser.add_argument('--date',required=True)
    args=parser.parse_args();day=date.fromisoformat(args.date)
    report={'stock':args.stock,'date':day.isoformat()}
    try: report['FinMind']=probe_finmind(args.stock,day)
    except (ProviderError,URLError,TimeoutError) as exc: report['FinMind']={'error':str(exc)}
    report['twstock']=probe_twstock(args.stock,day)
    report['tw_stocker']=probe_voidful(args.stock,day)
    report['stock_strategies_only']={'repository':'https://github.com/kevin801221/stock-strategies-only',
        'role':'strategy application; its stock data adapter uses FinMind',
        'note':'Repository code inspected, not executed.'}
    print(json.dumps(report,ensure_ascii=False,indent=2,default=str))
if __name__=='__main__': main()
