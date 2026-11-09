"""Build a transparent current universe from official snapshots, not a historical index."""
from datetime import datetime,timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json, re, hashlib
from urllib.request import Request,urlopen

MOPS='https://openapi.twse.com.tw/v1/opendata/t187ap03_L'
TPEX='https://www.tpex.org.tw/openapi/v1/tpex_daily_market_value'

def positive(value):
    try: v=Decimal(str(value).replace(',','').strip())
    except InvalidOperation: return None
    return v if v.is_finite() and v>0 else None

def rank(mops,tpex,quotes,day):
    if quotes.get('date')!=day.replace('-','') or quotes.get('stat')!='OK': raise ValueError('TWSE quote date mismatch')
    tables=[x for x in quotes['tables'] if all(y in x.get('fields',[]) for y in ('證券代號','證券名稱','收盤價'))]
    if len(tables)!=1: raise ValueError('missing or ambiguous TWSE quotes')
    t=tables[0]; f={name:t['fields'].index(name) for name in ('證券代號','證券名稱','收盤價')}
    price={r[f['證券代號']]:r for r in t['data']}
    results=[];excluded=[]
    for x in mops:
        code=x.get('公司代號','');name=x.get('公司簡稱','');q=price.get(code)
        if not re.fullmatch(r'\d{4}',code) or name.endswith('-DR'): continue
        shares=positive(x.get('已發行普通股數或TDR原股發行股數'));close=positive(q[f['收盤價']]) if q else None
        if not shares or not close:
            excluded.append({'market':'TWSE','code':code,'reason':'missing_close_or_shares'});continue
        report=x.get('出表日期','')
        if not re.fullmatch(r'\d{7}',report): raise ValueError('invalid MOPS report date')
        report_iso=f'{int(report[:3])+1911:04d}-{report[3:5]}-{report[5:]}'
        if report_iso>day: raise ValueError('MOPS snapshot later than quote date')
        results.append({'code':code,'name':name,'market':'TWSE','close':str(close),'shares':str(shares),
                        'market_value_twd':str(close*shares),'shares_report_date':report_iso})
    roc=f'{int(day[:4])-1911:03d}{day[5:7]}{day[8:]}'
    for x in tpex:
        code=x.get('SecuritiesCompanyCode','');name=x.get('CompanyName','')
        if x.get('Date')!=roc: raise ValueError('TPEx market value date mismatch')
        if not re.fullmatch(r'\d{4}',code) or name.endswith('-DR'): continue
        shares=positive(x.get('Capitals'));close=positive(x.get('ClosePrice'))
        if not shares or not close:
            excluded.append({'market':'TPEx','code':code,'reason':'missing_close_or_shares'});continue
        results.append({'code':code,'name':name,'market':'TPEx','close':str(close),'shares':str(shares),
                        'market_value_twd':str(close*shares),'shares_report_date':day})
    if len({r['code'] for r in results})!=len(results): raise ValueError('duplicate stock code')
    results.sort(key=lambda x:(-Decimal(x['market_value_twd']),x['code']))
    if len(results)<500: raise ValueError('less than 500 valid securities')
    for i,row in enumerate(results[:500],1): row['rank']=i
    return {'as_of':day,'selection':'近似市值排名：已發行普通股數 × 收盤價；排除 DR、ETF 與無收盤價證券',
            'limitations':['上市發行股數為 MOPS 前日快照，非保證當日股數','依資料檔日期選樣，非歷史每日成分股；不可視為無倖存者偏誤',
                           '未成交及停牌缺少收盤價者不納入排序'],
            'total_eligible':len(results),'excluded':excluded,'items':results[:500]}

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--day',default='2026-11-03');p.add_argument('--quotes',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    directory=Path(a.output);directory.mkdir(parents=True,exist_ok=True)
    payload={}
    for label,url in [('mops',MOPS),('tpex',TPEX)]:
        with urlopen(Request(url,headers={'User-Agent':'TaiwanMarginMonitor/1.0 research'}),timeout=60) as r: raw=r.read()
        (directory/f'{label}.json').write_bytes(raw)
        payload[label]=json.loads(raw)
        (directory/f'{label}.meta.json').write_text(json.dumps({'url':url,'fetched_at':datetime.now(timezone.utc).isoformat(),'sha256':hashlib.sha256(raw).hexdigest()},indent=2),encoding='utf8')
    quotes=json.loads(Path(a.quotes).read_text(encoding='utf8'))
    report=rank(payload['mops'],payload['tpex'],quotes,a.day)
    (directory/'universe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'as_of':a.day,'selected':len(report['items']),'eligible':report['total_eligible'],'excluded':len(report['excluded'])},ensure_ascii=False))
if __name__=='__main__':main()
