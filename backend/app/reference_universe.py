"""Archive FinMind stock-info and delisted snapshots for universe-gap analysis.

Neither is a verified point-in-time membership/industry history.  Archives are
immutable by capture timestamp and do not expose API credentials.
"""
import argparse,hashlib,json,os,time
from datetime import date,timezone
from pathlib import Path
from urllib.parse import urlencode
from .finmind import BASE_URL,fetch_raw,ProviderError,_validate_envelope

DATASETS=('TaiwanStockInfo','TaiwanStockDelisting')

def validate(raw,status,dataset):
    _validate_envelope(raw,status,dataset)
    rows=json.loads(raw)['data']
    if not rows:raise ProviderError(dataset+': empty list cannot establish universe coverage')
    for item in rows:
        if not isinstance(item,dict) or not isinstance(item.get('stock_id'),str) or not item['stock_id'].strip():
            raise ProviderError(dataset+': invalid stock identifier')
        if not isinstance(item.get('stock_name'),str):raise ProviderError(dataset+': invalid stock name')
        raw_date=item.get('date')
        # Current FinMind stock-info includes index-like entries whose date is
        # the literal string 'None'. Preserve but never assign a made-up date.
        if dataset=='TaiwanStockInfo' and raw_date in (None,'','None'):continue
        try:date.fromisoformat(raw_date)
        except (ValueError,TypeError,KeyError) as exc:raise ProviderError(dataset+': invalid date') from exc
    return rows

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',default='research/reference');p.add_argument('--interval',type=float,default=15)
    p.add_argument('--reuse-latest-archive',action='store_true',help='reuse the newest checksum-verified existing response')
    args=p.parse_args()
    if args.interval<12:p.error('interval must be at least 12 seconds')
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    results=[]
    fetched=None;new_fetches=0
    for idx,dataset in enumerate(DATASETS):
        url=BASE_URL+'?'+urlencode({'dataset':dataset})
        metas=sorted(out.glob(dataset+'_*.meta.json'),reverse=True) if args.reuse_latest_archive else []
        if metas:
            meta=json.loads(metas[0].read_text(encoding='utf8'))
            raw=(out/meta['filename']).read_text(encoding='utf8');status=meta['http_status']
            if meta['source_url']!=url or hashlib.sha256(raw.encode('utf8')).hexdigest()!=meta['sha256']:
                raise ProviderError(dataset+': archived response checksum/source mismatch')
        else:
            if new_fetches:time.sleep(args.interval)
            raw,status,fetched=fetch_raw(url,os.getenv('FINMIND_TOKEN'));new_fetches+=1
            stamp=fetched.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            path=out/(dataset+'_'+stamp+'.json');path.write_text(raw,encoding='utf8')
            meta={'source_url':url,'fetched_at':fetched.isoformat(),'http_status':status,
                  'sha256':hashlib.sha256(raw.encode('utf8')).hexdigest(),'filename':path.name}
            (out/(dataset+'_'+stamp+'.meta.json')).write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf8')
        rows=validate(raw,status,dataset)
        dates=[x['date'] for x in rows if x.get('date') not in (None,'','None')]
        results.append({**meta,'rows':len(rows),'unique_codes':len({x['stock_id'] for x in rows}),
          'missing_date_rows':len(rows)-len(dates),'earliest_row_date':min(dates) if dates else None,'latest_row_date':max(dates) if dates else None})
    from datetime import datetime
    report={'created_at':datetime.now(timezone.utc).isoformat(),'sources':results,
      'limitations':['stock_info 產業及市場不是歷史時點資料','下市清單不證明歷史上市日期、產業或市值，無法單獨修復存活者偏差',
        '資料來自事後取得的 FinMind 快照，無法證明歷史發布時間或避免後續修訂']}
    (out/'reference_snapshot.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps([{'dataset':x['filename'].split('_')[0],'rows':x['rows'],'unique_codes':x['unique_codes']} for x in results],ensure_ascii=False))

if __name__=='__main__':main()
