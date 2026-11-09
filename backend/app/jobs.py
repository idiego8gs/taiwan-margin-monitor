"""Durable leased jobs and provider-wide request reservations (no cloud compute)."""
from datetime import timedelta, timezone
import json,os,socket,time,uuid
from sqlalchemy import select,update,and_,or_
from .db import engine
from . import warehouse_schema as s
from .warehouse import now,encode,insert_ignore

def enqueue(kind,params,key=None,target_engine=engine,priority=100):
    key=key or f'{kind}:{uuid.uuid4()}';job_id=str(uuid.uuid4())
    with target_engine.begin() as c:
        insert_ignore(c,s.jobs,dict(id=job_id,dedupe_key=key,kind=kind,status='queued',params_json=encode(params),priority=priority,
            checkpoint_json='{}',attempts=0,max_attempts=5,next_attempt_at=now()),['dedupe_key'])
        c.execute(update(s.jobs).where(s.jobs.c.dedupe_key==key,s.jobs.c.priority>priority).values(priority=priority))
        return c.scalar(select(s.jobs.c.id).where(s.jobs.c.dedupe_key==key))
def claim(target_engine=engine,worker_id=None,lease_seconds=300,kinds=None,market=None):
    worker_id=worker_id or f'{socket.gethostname()}:{os.getpid()}'
    with target_engine.begin() as c:
        stamp=now()
        q=select(s.jobs).where(or_(and_(s.jobs.c.status.in_(['queued','retrying']),s.jobs.c.next_attempt_at<=stamp),
            and_(s.jobs.c.status=='running',s.jobs.c.lease_until<stamp))).order_by(s.jobs.c.priority,s.jobs.c.created_at).limit(1)
        if kinds:q=q.where(s.jobs.c.kind.in_(kinds))
        if market:q=q.where(s.jobs.c.params_json.like('%"market":"'+market+'"%'))
        if c.dialect.name=='postgresql':q=q.with_for_update(skip_locked=True)
        row=c.execute(q).mappings().first()
        if not row:return None
        if row['attempts']>=row['max_attempts']:
            c.execute(update(s.jobs).where(s.jobs.c.id==row['id']).values(status='failed',last_error='maximum attempts exceeded',updated_at=stamp))
            return None
        c.execute(update(s.jobs).where(s.jobs.c.id==row['id']).values(status='running',attempts=row['attempts']+1,
            worker_id=worker_id,heartbeat_at=stamp,lease_until=stamp+timedelta(seconds=lease_seconds),updated_at=stamp))
        return dict(row)|{'worker_id':worker_id,'attempts':row['attempts']+1}
def heartbeat(job,checkpoint=None,target_engine=engine,lease_seconds=300):
    stamp=now();values=dict(heartbeat_at=stamp,updated_at=stamp,lease_until=stamp+timedelta(seconds=lease_seconds))
    if checkpoint is not None:values['checkpoint_json']=encode(checkpoint)
    with target_engine.begin() as c:
        result=c.execute(update(s.jobs).where(s.jobs.c.id==job['id'],s.jobs.c.worker_id==job['worker_id'],s.jobs.c.status=='running').values(**values))
        if result.rowcount!=1:raise RuntimeError('job lease lost')
def finish(job,result=None,*,status='completed',error=None,retry_after=60,target_engine=engine):
    if status not in ('completed','needs_review','retrying','failed'):raise ValueError('invalid final job state')
    with target_engine.begin() as c:
        values=dict(status=status,result_json=encode(result or {}),last_error=error,updated_at=now(),lease_until=None,
            next_attempt_at=now()+timedelta(seconds=retry_after))
        r=c.execute(update(s.jobs).where(s.jobs.c.id==job['id'],s.jobs.c.worker_id==job['worker_id'],s.jobs.c.status=='running').values(**values))
        if r.rowcount!=1:raise RuntimeError('job lease lost')
def reserve_provider(provider,interval=15,target_engine=engine,sleep=time.sleep):
    """Reserve before sleeping so all cooperating processes share the same budget."""
    with target_engine.begin() as c:
        insert_ignore(c,s.limits,dict(provider=provider,next_allowed_at=now(),interval_seconds=interval),['provider'])
        q=select(s.limits).where(s.limits.c.provider==provider)
        if c.dialect.name=='postgresql':q=q.with_for_update()
        row=c.execute(q).mappings().one();stamp=now();previous=row['next_allowed_at']
        if previous.tzinfo is None:previous=previous.replace(tzinfo=timezone.utc)
        scheduled=max(stamp,previous)
        c.execute(update(s.limits).where(s.limits.c.provider==provider).values(
            next_allowed_at=scheduled+timedelta(seconds=max(interval,row['interval_seconds']))))
    wait=max(0,(scheduled-stamp).total_seconds())
    if wait:sleep(wait)
    return wait

def defer_provider(provider,seconds,target_engine=engine):
    with target_engine.begin() as c:
        insert_ignore(c,s.limits,dict(provider=provider,next_allowed_at=now(),interval_seconds=15 if provider=='FinMind' else 3),['provider'])
        q=select(s.limits).where(s.limits.c.provider==provider)
        if c.dialect.name=='postgresql':q=q.with_for_update()
        row=c.execute(q).mappings().one();previous=row['next_allowed_at']
        if previous.tzinfo is None:previous=previous.replace(tzinfo=timezone.utc)
        c.execute(update(s.limits).where(s.limits.c.provider==provider).values(next_allowed_at=max(previous,now()+timedelta(seconds=seconds))))

def coalesce_event_jobs(target_engine=engine):
    """Supersede queued narrower ranges only when another queued/running job covers them."""
    with target_engine.begin() as c:
        query=select(s.jobs).where(s.jobs.c.kind=='finmind_events',s.jobs.c.status.in_(['queued','running','retrying']))
        if c.dialect.name=='postgresql':query=query.with_for_update(skip_locked=True)
        rows=c.execute(query).mappings().all();groups={}
        for row in rows:
            try:
                p=json.loads(row['params_json']);key=p['security_id'];start=p['start'];end=p['end']
            except (ValueError,KeyError,TypeError):continue
            groups.setdefault(key,[]).append((row,start,end))
        changed=0
        for entries in groups.values():
            for row,start,end in entries:
                if row['status'] not in ('queued','retrying'):continue
                candidates=[other for other,a,b in entries if other['id']!=row['id'] and a<=start and b>=end
                    and (a<start or b>end or other['created_at']<row['created_at'])]
                if candidates:
                    replacement=min(candidates,key=lambda x:x['created_at'])
                    c.execute(update(s.jobs).where(s.jobs.c.id==row['id']).values(status='superseded',
                        result_json=encode({'superseded_by':replacement['id'],'reason':'covering_event_range'}),updated_at=now()))
                    changed+=1
        return changed

def restore_superseded(covering_job_id,target_engine=engine):
    """A failed broader download must not silently erase narrower work."""
    with target_engine.begin() as c:
        q=select(s.jobs).where(s.jobs.c.kind=='finmind_events',s.jobs.c.status=='superseded')
        if c.dialect.name=='postgresql':q=q.with_for_update(skip_locked=True)
        restored=0
        for row in c.execute(q).mappings():
            result=json.loads(row['result_json'] or '{}')
            if result.get('superseded_by')!=covering_job_id:continue
            checkpoint=json.loads(row['checkpoint_json'] or '{}')
            checkpoint['reactivated_after_cover_failure']=covering_job_id
            c.execute(update(s.jobs).where(s.jobs.c.id==row['id']).values(status='queued',
                checkpoint_json=encode(checkpoint),next_attempt_at=now(),updated_at=now()))
            restored+=1
        return restored
