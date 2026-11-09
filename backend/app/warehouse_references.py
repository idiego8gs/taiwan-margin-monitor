"""Load archived calendars, index observations and listing evidence with provenance."""
from datetime import date
from decimal import Decimal
from pathlib import Path
import json,hashlib
from sqlalchemy import select
from .db import engine
from . import warehouse_schema as s
from .warehouse import record_capture,insert_ignore,encode,issue,upsert,digest
from .warehouse_ingest import ROOT,TOP,read,dt,event_records

def load(target_engine=engine):
    with target_engine.begin() as c:
        lookup={r['identity_key']:r['id'] for r in c.execute(select(s.securities)).mappings()}
        for market,filename in [('TWSE','taiex_202110_202611.json'),('TPEx','tpex_202110_202611.json')]:
            path=ROOT/'research/benchmark'/filename;data=read(path);raw=path.read_text(encoding='utf8')
            # This capture is a derived archive with its original monthly hashes/URLs retained.
            cid=record_capture(c,raw,key='index-derived:'+hashlib.sha256(raw.encode()).hexdigest(),provider=market,
                dataset='archived_price_index_projection',market=market,url=data['sources'][0]['url'],
                start=date.fromisoformat(data['start']),end=date.fromisoformat(data['end']),request={'original_sources':data['sources']})
            originals={hashlib.sha256(p.read_bytes()).hexdigest():p for p in path.parent.glob('*index_*.json')}
            for source in data['sources']:
                original=originals.get(source['sha256'])
                if not original:continue
                record_capture(c,original.read_text(encoding='utf8'),key='index-original:'+source['sha256'],provider=market,
                    dataset='official_monthly_price_index',market=market,url=source['url'],fetched_at=dt(source['fetched_at']))
            for day,row in data['days'].items():
                day=date.fromisoformat(day)
                insert_ignore(c,s.calendar,dict(market=market,trading_date=day,is_open=True,source_key=f'index:{cid}'),['market','trading_date','source_key'])
                insert_ignore(c,s.indices,dict(market=market,trading_date=day,index_kind='price_not_total_return',close=Decimal(row['close']),capture_id=cid),['market','trading_date','index_kind','capture_id'])
        # Current-market listing dates are conservative boundaries, not full historical lifecycles.
        sources=[('TWSE',TOP/'mops.json','公司代號','上市日期'),('TPEx',TOP/'tpex_basic_20261104.json','SecuritiesCompanyCode','DateOfListing')]
        for market,path,codekey,datekey in sources:
            raw=path.read_text(encoding='utf8')
            url='https://openapi.twse.com.tw/v1/opendata/t187ap03_L' if market=='TWSE' else 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O'
            cid=record_capture(c,raw,key='listing-snapshot:'+hashlib.sha256(raw.encode()).hexdigest(),provider=market,
                dataset='official_current_security_profile',market=market,url=url)
            for r in read(path):
                sid=lookup.get(f'current:{market}:{r.get(codekey)}');v=str(r.get(datekey,''))
                if not sid or len(v)!=8 or not v.isdigit():continue
                try:day=date(int(v[:4]),int(v[4:6]),int(v[6:]))
                except ValueError:continue
                upsert(c,s.states,dict(security_id=sid,kind='current_market_listing',valid_from=day,valid_to=None,
                    value_json=encode({'record':r,'capture_id':cid,'archive':str(path.relative_to(ROOT)),'historical_market_lifecycle_verified':False}),
                    source_key=f'current-listing:{market}:{sid}',verified=False),['source_key'])
        p=ROOT/'research/reference/par_value_summary.json'
        if p.exists():
            summary=read(p);source=summary['source'];rawpath=p.parent/source['filename']
            if rawpath.exists():
                raw=rawpath.read_text(encoding='utf8')
                if hashlib.sha256(raw.encode()).hexdigest()!=source['sha256_utf8']:raise ValueError('par value hash mismatch')
                cid=record_capture(c,raw,key='par-value:'+source['sha256_utf8'],provider='FinMind',dataset='TaiwanStockParValueChange',
                    url=source['source_url'],fetched_at=dt(source['fetched_at']))
                for row in json.loads(raw)['data']:
                    matching=[sid for key,sid in lookup.items() if key.startswith('current:') and key.endswith(':'+row['stock_id'])]
                    for sid in matching:event_records(c,sid,cid,'TaiwanStockParValueChange',encode({'data':[row]}))
        print('calendar/index/listing references loaded',flush=True)
        report=ROOT/'research/factor_validation_final.json'
        if report.exists():
            data=read(report);run_id=digest(data)
            insert_ignore(c,s.runs,dict(id=run_id,data_version=str(data.get('input_sha256',data.get('data_sha256','legacy-frozen-snapshot'))),
                protocol_json=encode({'legacy_file':str(report.relative_to(ROOT)),'sha256':hashlib.sha256(report.read_bytes()).hexdigest(),
                    'original_metadata':{k:v for k,v in data.items() if k not in ('results','items')},'not_new_warehouse_backtest':True}),status='exploratory_archived'),['id'])
            for i,row in enumerate(data.get('results',data.get('items',[]))):
                insert_ignore(c,s.results,dict(run_id=run_id,result_key=f'legacy:{i}',metrics_json=encode(row)),['run_id','result_key'])
if __name__=='__main__':load()
