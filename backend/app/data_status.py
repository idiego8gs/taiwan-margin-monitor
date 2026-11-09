"""Database-backed monitoring; data freshness and process leases are separate."""
import json
from sqlalchemy import select,func,case,and_
from . import warehouse_schema as s
from .warehouse import now

def status(conn):
    check=conn.execute(select(s.checks).where(s.checks.c.name=='coverage')).mappings().first()
    coverage=json.loads(check['summary_json']) if check else {'groups':{},'issues':{}}
    top=coverage['groups'].get('top500',{}).get('summary',{})
    snapshot=conn.execute(select(s.snapshots).order_by(s.snapshots.c.created_at.desc()).limit(1)).mappings().first()
    latest=conn.scalar(select(func.max(s.prices.c.trading_date)).where(s.prices.c.is_current==True))
    running=conn.execute(select(s.jobs.c.id,s.jobs.c.kind,s.jobs.c.status,s.jobs.c.heartbeat_at,s.jobs.c.lease_until)
        .where(s.jobs.c.status.in_(['running','retrying','queued'])).order_by(s.jobs.c.created_at).limit(20)).mappings().all()
    effective=case((and_(s.jobs.c.status=='running',s.jobs.c.lease_until<now()),'interrupted'),else_=s.jobs.c.status)
    work=[dict(kind=k,status=state,count=n) for k,state,n in conn.execute(select(s.jobs.c.kind,effective,func.count()).group_by(s.jobs.c.kind,effective))]
    research=conn.execute(select(s.runs.c.id,s.runs.c.status,s.runs.c.data_version,s.executions.c.heartbeat_at,s.executions.c.progress_json)
        .join(s.executions,s.executions.c.run_id==s.runs.c.id).order_by(s.runs.c.created_at.desc()).limit(1)).mappings().first()
    research=dict(research) if research else None
    if research:research['progress']=json.loads(research.pop('progress_json'))
    flags=['publication_times_unverified','historical_identity_unverified','uncalibrated']
    if snapshot and json.loads(snapshot['summary_json']).get('model_version')=='warehouse-close-0.6-v1':flags.append('legacy_model_missing_par_value_events')
    return dict(available=True,selected=top.get('selected',0),as_of=latest,latest_data_date=latest,
        stages={k:top.get(k,0) for k in ('downloaded','imported','verified','computable')},
        groups={k:v['summary'] for k,v in coverage['groups'].items()},issues=coverage['issues'],
        last_updated=check['checked_at'] if check else None,active_jobs=[dict(x) for x in running],work_summary=work,
        published_snapshot=dict(snapshot) if snapshot else None,research_run=research,
        status='research_only' if snapshot else 'incomplete',calibration='unavailable_scope_not_verified',
        sources=['TWSE','TPEx','FinMind'],quality_flags=flags,
        price_margin_completed=top.get('imported',0),price_repaired=0,repaired_missing_margin=0,
        events_completed=top.get('downloaded',0),prices_finished=False,events_finished=top.get('downloaded',0)==top.get('selected',-1),
        errors={},event_errors={},checked_at=now())

def quality(conn,code=None,kind=None,state=None,page=1,limit=50):
    q=select(s.issues,s.securities.c.code).outerjoin(s.securities,s.issues.c.security_id==s.securities.c.id)
    if code:q=q.where(s.securities.c.code==code)
    if kind:q=q.where(s.issues.c.kind==kind)
    if state:q=q.where(s.issues.c.status==state)
    total=conn.scalar(select(func.count()).select_from(q.subquery()))
    rows=conn.execute(q.order_by(s.issues.c.id).offset((page-1)*limit).limit(limit)).mappings().all()
    return {'items':[dict(r)|{'evidence':json.loads(r['evidence_json'])} for r in rows],'total':total,'page':page,'limit':limit}
