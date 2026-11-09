"""Optional targeted FinMind re-fetch; all callers use the shared database limiter."""
from datetime import date
from decimal import Decimal
import json,os
from sqlalchemy import select
from .db import engine,ProviderResponse,Session
from . import warehouse_schema as s
from .warehouse import record_capture,revision_rows,now
from .warehouse_ingest import rows_for
from .finmind import PRICE,MARGIN,EVENTS,fetch_raw,request_url,parse_response,_validate_envelope
from .jobs import reserve_provider

def repair(params):
    sid=params['security_id'];start=date.fromisoformat(params['start']);end=date.fromisoformat(params['end'])
    with engine.connect() as c:sec=c.execute(select(s.securities,s.listings.c.market).join(s.listings,s.listings.c.security_id==s.securities.c.id).where(s.securities.c.id==sid)).mappings().one()
    caps={};rows={};times={}
    for dataset in (PRICE,MARGIN):
        url=request_url(dataset,sec['code'],start,end);raw,status,stamp=fetch_raw(url,os.getenv('FINMIND_TOKEN'))
        with engine.begin() as c:cid=record_capture(c,raw,key=f'finmind:{sid}:{dataset}:{stamp.isoformat()}',provider='FinMind',dataset=dataset,
            code=sec['code'],market=sec['market'],url=url,start=start,end=end,fetched_at=stamp,http_status=status)
        if status==429:
            from .official_repair import SourceFailure
            raise SourceFailure('FinMind rate limited',900)
        rows[dataset]=parse_response(raw,status,dataset,sec['code'],start,end);caps[dataset]=cid;times[dataset]=stamp
    p,m=rows_for(sid,sec['market'],'FinMind',rows[PRICE],rows[MARGIN],caps,times,'finmind_documented_daily')
    with engine.begin() as c:count=revision_rows(c,s.prices,p)+revision_rows(c,s.margins,m)
    return {'code':sec['code'],'new_versions':count}

def repair_events(params):
    """Full-range archives preserve compatibility with the audited action loader."""
    from .warehouse_ingest import event_records
    from .warehouse import digest
    import hashlib
    sid=params['security_id'];start=date.fromisoformat(params['start']);end=date.fromisoformat(params['end'])
    with engine.connect() as c:sec=c.execute(select(s.securities,s.listings.c.market).join(s.listings,s.listings.c.security_id==s.securities.c.id).where(s.securities.c.id==sid)).mappings().one()
    for dataset in EVENTS:
        with engine.connect() as c:
            found=c.scalar(select(ProviderResponse.id).where(ProviderResponse.code==sec['code'],ProviderResponse.dataset==dataset,
                ProviderResponse.start_date<=start,ProviderResponse.end_date>=end,ProviderResponse.http_status==200))
        if found:continue
        url=request_url(dataset,sec['code'],start,end);raw,status,stamp=fetch_raw(url,os.getenv('FINMIND_TOKEN'))
        with engine.begin() as c:cid=record_capture(c,raw,key=f'finmind-event:{sid}:{dataset}:{stamp.isoformat()}',provider='FinMind',
            dataset=dataset,code=sec['code'],market=sec['market'],url=url,start=start,end=end,fetched_at=stamp,http_status=status)
        if status==429:
            from .official_repair import SourceFailure
            raise SourceFailure('FinMind rate limited',900)
        _validate_envelope(raw,status,dataset)
        if any(row.get('stock_id')!=sec['code'] for row in json.loads(raw)['data']):raise ValueError('event stock mismatch')
        with engine.begin() as c:
            event_records(c,sid,cid,dataset,raw)
            c.execute(ProviderResponse.__table__.insert(),dict(provider='FinMind',dataset=dataset,code=sec['code'],start_date=start,end_date=end,
                fetched_at=stamp,imported_at=stamp,capture_mode='live',source_url=url,http_status=status,sha256=hashlib.sha256(raw.encode()).hexdigest(),payload=raw))
    return {'code':sec['code'],'event_coverage_end':str(end)}
