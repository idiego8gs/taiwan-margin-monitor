"""Archive documented all-market reference prices for par-value changes.

Reference-price ratios do not establish the correct adjustment of each margin
account or lot. No synthetic transaction is generated from this source.
"""
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
import hashlib,json,os
from .finmind import BASE_URL,fetch_raw,_validate_envelope,ProviderError

def main():
    dataset='TaiwanStockParValueChange';start='2020-01-01'
    url=BASE_URL+'?'+urlencode({'dataset':dataset,'start_date':start})
    raw,status,fetched=fetch_raw(url,os.getenv('FINMIND_TOKEN'))
    out=Path('research/reference');out.mkdir(parents=True,exist_ok=True)
    stamp=fetched.strftime('%Y%m%dT%H%M%S%fZ');name=dataset+'_'+stamp+'.json'
    (out/name).write_text(raw,encoding='utf8')
    meta={'source_url':url,'http_status':status,'fetched_at':fetched.isoformat(),'sha256_utf8':hashlib.sha256(raw.encode('utf8')).hexdigest(),'filename':name}
    (out/(name+'.meta.json')).write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf8')
    _validate_envelope(raw,status,dataset);rows=json.loads(raw)['data']
    if not rows:raise ProviderError(dataset+': empty response requires review')
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('stock_id'),str):raise ProviderError(dataset+': invalid stock id')
        try:day=date.fromisoformat(row['date'])
        except (KeyError,ValueError,TypeError) as exc:raise ProviderError(dataset+': invalid date') from exc
        if day<date.fromisoformat(start):raise ProviderError(dataset+': date before request')
    codes={x['code'] for x in json.loads(Path('research/top500_20261103/universe.json').read_text(encoding='utf8'))['items']}
    selected=[x for x in rows if x['stock_id'] in codes and '2021-10-01'<=x['date']<='2026-11-03']
    report={'source':meta,'rows':len(rows),'top500_events':selected,
      'limitation':'上市／上櫃清單與來源可能事後修訂；參考價不可單獨推導股數與借款調整或視為完整公司行動。'}
    (out/'par_value_summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'all_events':len(rows),'top500_events':len(selected),'unique_top500_codes':len({x['stock_id'] for x in selected})}))

if __name__=='__main__':main()
