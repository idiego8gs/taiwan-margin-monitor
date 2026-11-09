"""Shared exact-value, lineage and revision helpers for warehouse ingestion."""
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from sqlalchemy import select, update
from . import warehouse_schema as s

PARSER='warehouse-v1'
def now():return datetime.now(timezone.utc)
def encode(value):
    def default(v):
        if isinstance(v,Decimal):return format(v.normalize(),'f')
        if isinstance(v,(date,datetime)):return v.isoformat()
        raise TypeError(type(v).__name__)
    return json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':'),default=default)
def digest(value):return sha256(encode(value).encode()).hexdigest()
def insert_ignore(conn,table,values,keys):
    if conn.dialect.name=='postgresql':from sqlalchemy.dialects.postgresql import insert
    elif conn.dialect.name=='sqlite':from sqlalchemy.dialects.sqlite import insert
    else:raise RuntimeError('unsupported warehouse dialect')
    return conn.execute(insert(table).values(values).on_conflict_do_nothing(index_elements=keys))
def upsert(conn,table,values,keys,changes=None):
    if conn.dialect.name=='postgresql':from sqlalchemy.dialects.postgresql import insert
    else:from sqlalchemy.dialects.sqlite import insert
    stmt=insert(table).values(values)
    changes=changes or [k for k in values if k not in keys]
    conn.execute(stmt.on_conflict_do_update(index_elements=keys,set_={k:getattr(stmt.excluded,k) for k in changes}))

def record_capture(conn,raw,*,key,provider,dataset,url,code=None,market=None,start=None,end=None,
                   fetched_at=None,http_status=200,published_at=None,request=None):
    checksum=sha256(raw.encode('utf8')).hexdigest()
    existing=conn.execute(select(s.captures.c.id,s.captures.c.sha256).where(s.captures.c.capture_key==key)).first()
    if existing:
        if existing.sha256!=checksum:raise ValueError('immutable capture key changed')
        return existing.id
    insert_ignore(conn,s.blobs,dict(sha256=checksum,payload=raw,encoding='utf-8-text',bytes_count=len(raw.encode('utf8'))),['sha256'])
    values=dict(capture_key=key,provider=provider,dataset=dataset,code=code,market=market,
        start_date=start,end_date=end,source_url=url,fetched_at=fetched_at,published_at=published_at,
        availability='captured_live' if published_at else 'retrospective_publication_unknown',
        http_status=http_status,sha256=checksum,parser_version=PARSER,request_json=encode(request or {}))
    return conn.execute(s.captures.insert().values(**values).returning(s.captures.c.id)).scalar_one()

def security_id(conn,code,name,identity_key,status='historical_identity_unverified'):
    insert_ignore(conn,s.securities,dict(identity_key=identity_key,code=code,name=name,identity_status=status),['identity_key'])
    return conn.scalar(select(s.securities.c.id).where(s.securities.c.identity_key==identity_key))

def issue(conn,key,kind,*,sid=None,day=None,evidence=None,status='needs_review',severity='high'):
    values=dict(issue_key=key,kind=kind,security_id=sid,trading_date=day,status=status,severity=severity,
        evidence_json=encode(evidence or {}),last_seen_at=now())
    upsert(conn,s.issues,values,['issue_key'],['status','evidence_json','last_seen_at','severity'])

def revision_rows(conn,table,rows):
    """Append changed values; a later import of an older capture cannot become current."""
    if not rows:return 0
    ids={r['security_id'] for r in rows}
    if conn.dialect.name=='postgresql':
        conn.execute(select(s.securities.c.id).where(s.securities.c.id.in_(sorted(ids))).order_by(s.securities.c.id).with_for_update()).all()
    existing_rows=conn.execute(select(table.c.id,table.c.security_id,table.c.trading_date,table.c.provider,
        table.c.basis,table.c.value_sha256,table.c.observed_at,table.c.is_current).where(table.c.security_id.in_(ids))).mappings().all()
    key=lambda r:tuple(r[k] for k in ('security_id','trading_date','provider','basis'))
    current={key(r):r for r in existing_rows if r['is_current']}
    versions={(*key(r),r['value_sha256']):r for r in existing_rows}
    additions=[];deactivate=[];reactivate=[]
    for row in rows:
        k=key(row);found=current.get(k)
        if found and found['value_sha256']==row['value_sha256']:continue
        # NULL publication time does not become historical knowledge; observed_at is capture time only.
        oldtime=found['observed_at'] if found else None
        if oldtime and oldtime.tzinfo is None:oldtime=oldtime.replace(tzinfo=timezone.utc)
        row['is_current']=not oldtime or row['observed_at']>=oldtime
        existing=versions.get((*k,row['value_sha256']))
        if existing:
            if row['is_current']:
                if found:deactivate.append(found['id'])
                reactivate.append(existing['id']);current[k]=existing
            continue
        if row['is_current'] and found:deactivate.append(found['id'])
        additions.append(row)
    for offset in range(0,len(deactivate),500):
        conn.execute(update(table).where(table.c.id.in_(deactivate[offset:offset+500])).values(is_current=False))
    for offset in range(0,len(reactivate),500):
        conn.execute(update(table).where(table.c.id.in_(reactivate[offset:offset+500])).values(is_current=True))
    for offset in range(0,len(additions),500):conn.execute(table.insert(),additions[offset:offset+500])
    return len(additions)

def require_schema(engine):
    from sqlalchemy import inspect
    if not inspect(engine).has_table('observations'):
        raise RuntimeError('Database not migrated. Run: python -m alembic upgrade head')
