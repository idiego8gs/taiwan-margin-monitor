"""Build a conservative research queue from official TWSE/TPEx delisting lists.

The queue is NOT historical top-500 membership or proof of a stock's complete
listing history. Currently listed, ambiguous, reused and duplicate codes are
excluded, and results are kept separate from the current 500 universe.
"""
from collections import defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,urlopen
import argparse,hashlib,json,re,time

TWSE='https://openapi.twse.com.tw/v1/company/suspendListingCsvAndHtml'
TPEX='https://www.tpex.org.tw/www/zh-tw/company/deListed'
EXPECTED_TPEX_FIELDS=['股票代號','公司名稱','終止上櫃日期','終止上櫃原因','公司資料網址']

def roc_date(value):
    try:
        y,m,d=map(int,re.split(r'[-/]',value))
        if y<1 or y>999:raise ValueError('invalid ROC year')
        return date(y+1911,m,d)
    except (ValueError,TypeError) as exc:raise ValueError('invalid official delisting date') from exc

def archive(url,output,label):
    req=Request(url,headers={'User-Agent':'TaiwanMarginMonitor/0.1 research','Referer':'https://www.tpex.org.tw/'})
    with urlopen(req,timeout=45) as response:
        raw=response.read();status=response.status
    if status!=200:raise ValueError('official HTTP '+str(status))
    data=json.loads(raw.decode('utf-8-sig'))
    stamp=datetime.now(timezone.utc);name=label+'_'+stamp.strftime('%Y%m%dT%H%M%S%fZ')
    (output/(name+'.json')).write_bytes(raw)
    meta={'source_url':url,'captured_at':stamp.isoformat(),'sha256':hashlib.sha256(raw).hexdigest(),'filename':name+'.json'}
    (output/(name+'.meta.json')).write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf8')
    return data,meta

def official_rows(twse,tpex,first,last):
    if not isinstance(twse,list):raise ValueError('unexpected TWSE response')
    rows=[]
    for r in twse:
        if not isinstance(r,dict) or not {'Code','Company','DelistingDate'}<=r.keys():raise ValueError('unexpected TWSE fields')
        day=roc_date(r['DelistingDate'])
        if first<=day<=last:rows.append({'code':r['Code'].strip(),'name':r['Company'].strip(),'market':'TWSE','end_date':day.isoformat(),'reason':None})
    for year,j in tpex.items():
        if j.get('stat')!='ok' or str(j.get('date'))!=str(year) or len(j.get('tables',[]))!=1:raise ValueError('unexpected TPEx envelope')
        table=j['tables'][0]
        if table.get('fields')!=EXPECTED_TPEX_FIELDS or not isinstance(table.get('data'),list) or int(table['totalCount'])!=len(table['data']):
            raise ValueError('unexpected or incomplete TPEx table')
        for raw in table['data']:
            if not isinstance(raw,list) or len(raw)!=len(EXPECTED_TPEX_FIELDS):raise ValueError('unexpected TPEx row')
            code,name,when,reason,_=raw;day=roc_date(when)
            if day.year!=year:raise ValueError('TPEx response contains wrong year')
            if first<=day<=last:rows.append({'code':code.strip(),'name':name.strip(),'market':'TPEx','end_date':day.isoformat(),'reason':reason})
    return rows

def select_queue(rows,active):
    by_code=defaultdict(list)
    for row in rows:
        if re.fullmatch(r'[0-9]{4}',row['code']):by_code[row['code']].append(row)
    queue=[];excluded=[]
    for code,items in by_code.items():
        if code in active:excluded.append({'code':code,'reason':'active_in_official_snapshot','records':items})
        elif len(items)!=1:excluded.append({'code':code,'reason':'ambiguous_or_reused_code','records':items})
        else:queue.append(items[0])
    return sorted(queue,key=lambda x:(x['end_date'],x['code'])),excluded

def main():
    p=argparse.ArgumentParser();p.add_argument('--start',default='2021-10-01');p.add_argument('--end',default='2026-11-03');p.add_argument('--output',default='research/delisted_202110_202611');a=p.parse_args()
    first=date.fromisoformat(a.start);last=date.fromisoformat(a.end)
    if first>last:raise ValueError('end before start')
    out=Path(a.output);raw_dir=out/'official_raw';raw_dir.mkdir(parents=True,exist_ok=True)
    twse,twse_meta=archive(TWSE,raw_dir,'twse_delisting');tpex={};sources=[twse_meta]
    for year in range(first.year,last.year+1):
        url=TPEX+'?'+urlencode({'date':str(year),'reason':'-1','response':'json'})
        tpex[year],meta=archive(url,raw_dir,'tpex_delisting_'+str(year));sources.append(meta);time.sleep(.5)
    rows=official_rows(twse,tpex,first,last)
    root=Path('research/top500_20261103')
    twse_active=json.loads((root/'mops.json').read_text(encoding='utf8'))
    tpex_active=json.loads((root/'tpex_basic_20261104.json').read_text(encoding='utf8'))
    active={str(r.get('公司代號','')).strip() for r in twse_active}|{str(r.get('SecuritiesCompanyCode','')).strip() for r in tpex_active}
    active|={r['code'] for r in json.loads((root/'universe.json').read_text(encoding='utf8'))['items']}
    queue,excluded=select_queue(rows,active)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'start':first.isoformat(),'end':last.isoformat(),
      'sources':sources,'official_rows_in_period':len(rows),'excluded_count':len(excluded),'items':queue,'excluded':excluded,
      'limitations':['官方名單為事後擷取，非時點歷史市值排名；不構成歷史前 500 檔成分',
       '僅排除現有官方基本資料出現代號；仍無法完全識別所有歷史代號重用或轉板',
       '下載僅保存供應商原始回應，不直接寫入共用研究資料庫；須逐檔驗證市場、存續及事件',
       '下市櫃日並非最後可成交日；成交與公司行動限制尚待查證']}
    (out/'universe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'official_rows_in_period':len(rows),'active_or_ambiguous_excluded':len(excluded),'queue':len(queue)},ensure_ascii=False))

if __name__=='__main__':main()
