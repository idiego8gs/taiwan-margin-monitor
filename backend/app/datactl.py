"""Local-only administration and durable worker. python -m app.datactl --help."""
from datetime import date,datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse,json,os,time,threading,subprocess
from sqlalchemy import select
from .db import engine
from . import warehouse_schema as s
from .warehouse import encode,now,upsert
from .jobs import enqueue,claim,heartbeat,finish

def queue_events(through,target_engine=engine):
    with target_engine.connect() as c:
        ids=list(c.scalars(select(s.securities.c.id).where(s.securities.c.identity_key.like('current:%'))))
    for sid in ids:enqueue('finmind_events',{'security_id':sid,'start':'2021-10-01','end':through},f'events:{sid}:{through}',target_engine,priority=50)
    from .jobs import coalesce_event_jobs
    coalesce_event_jobs(target_engine)
    return {'queued_or_reused':len(ids),'through':through}

def queue_daily_followups(day,target_engine=engine):
    with target_engine.connect() as c:
        states=list(c.scalars(select(s.jobs.c.status).where(s.jobs.c.dedupe_key.in_([f'daily:TWSE:{day}',f'daily:TPEx:{day}']))))
    if len(states)!=2 or any(x!='completed' for x in states):return False
    queue_events(day,target_engine)
    enqueue('audit',{},f'daily-audit:{day}',target_engine,priority=10)
    enqueue('compute',{},f'daily-compute:{day}',target_engine,priority=15)
    return True

def handler(job):
    params=json.loads(job['params_json']);kind=job['kind']
    if kind=='audit':
        from .warehouse_quality import audit
        return audit(progress=lambda p:heartbeat(job,p))
    if kind=='compute':
        from .warehouse_compute import compute
        return compute(progress=lambda p:heartbeat(job,p))
    if kind=='official_day':
        from .official_repair import repair_day
        return repair_day(params['market'],date.fromisoformat(params['date']),params.get('quotes',False),params.get('force',False))
    if kind=='finmind_window':
        from .provider_repair import repair
        return repair(params)
    if kind=='finmind_events':
        from .provider_repair import repair_events
        return repair_events(params)
    raise ValueError('unknown job kind '+kind)

def run_one(kinds=None,market=None):
    job=claim(kinds=kinds,market=market)
    if not job:return False
    stop=threading.Event()
    def pulse():
        while not stop.wait(30):
            try:heartbeat(job)
            except Exception:return
    thread=threading.Thread(target=pulse,daemon=True);thread.start()
    try:
        result=handler(job);finish(job,result)
        if job['kind'] in ('official_day','finmind_events'):
            from sqlalchemy import func
            with engine.connect() as c:
                pending=c.scalar(select(func.count()).select_from(s.jobs).where(s.jobs.c.kind==job['kind'],s.jobs.c.status.in_(['queued','running','retrying'])))
                completed=c.scalar(select(func.count()).select_from(s.jobs).where(s.jobs.c.kind==job['kind'],s.jobs.c.status=='completed'))
            if pending==0:
                enqueue('compute',{},f"post-{job['kind']}-compute:{completed}",priority=15)
                enqueue('audit',{},f"post-{job['kind']}-audit:{completed}",priority=20)
            elif job['kind']=='official_day' and completed%100==0:
                enqueue('audit',{},f'official-audit:{completed}',priority=10)
        if job['kind']=='official_day' and job['dedupe_key'].startswith('daily:'):
            # Once both exchanges have complete daily quote/margin reports, queue a review.
            params=json.loads(job['params_json']);day=params['date']
            queue_daily_followups(day)
        print(encode({'job':job['id'],'kind':job['kind'],'status':'completed'}),flush=True)
    except Exception as exc:
        from .official_repair import SourceFailure,NotPublished
        waiting=isinstance(exc,NotPublished) and job['dedupe_key'].startswith('daily:')
        retry=waiting or (not isinstance(exc,(ValueError,KeyError)) and job['attempts']<job['max_attempts'])
        state='retrying' if retry else 'needs_review'
        delay=getattr(exc,'retry_after',900 if isinstance(exc,NotPublished) else min(3600,60*2**job['attempts']))
        finish(job,status=state,error=f'{type(exc).__name__}: {exc}',retry_after=delay)
        if state=='needs_review' and job['kind']=='finmind_events':
            from .jobs import restore_superseded
            restore_superseded(job['id'])
        print(encode({'job':job['id'],'kind':job['kind'],'status':state,'error':str(exc)}),flush=True)
    finally:stop.set();thread.join(timeout=1)
    return True

def schedule():
    local=datetime.now(ZoneInfo('Asia/Taipei'))
    # Market reports still verify the date and schema; weekday alone does not prove a trading day.
    if local.weekday()>4 or local.hour<20:return
    day=local.date()
    for market in ('TWSE','TPEx'):
        enqueue('official_day',{'market':market,'date':str(day),'quotes':True},f'daily:{market}:{day}',priority=0)
    with engine.begin() as c:upsert(c,s.checks,dict(name='scheduler',summary_json=encode({'checked_at':now(),'date':day}),checked_at=now()),['name'])

def backup(destination):
    root=Path(__file__).resolve().parents[2];config=json.loads((root/'.local/database.json').read_text())
    target=Path(destination).resolve();target.parent.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy();env['PGPASSWORD']=config['app_password']
    import hashlib
    from sqlalchemy import text,func
    # Counts and pg_dump share one repeatable-read snapshot even while workers write.
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as c:
        snapshot=c.scalar(text('SELECT pg_export_snapshot()'))
        counts={name:c.scalar(select(func.count()).select_from(table)) for name,table in s.Base.metadata.tables.items()}
        counts['alembic_version']=c.scalar(text('SELECT count(*) FROM alembic_version'))
        subprocess.run([str(root/'.local/pgsql/bin/pg_dump.exe'),'-h','127.0.0.1','-p',str(config['port']),'-U','margin','-d','margin','-Fc',
            '--snapshot',snapshot,'-f',str(target)],env=env,check=True)
    result={'path':str(target),'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'table_counts':counts}
    Path(str(target)+'.manifest.json').write_text(encode(result),encoding='utf8')
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    for name in ('audit','compute'):sub.add_parser(name)
    repair=sub.add_parser('queue-repairs');repair.add_argument('--limit',type=int)
    w=sub.add_parser('worker');w.add_argument('--once',action='store_true');w.add_argument('--drain',action='store_true');w.add_argument('--kinds',help='Comma-separated job kinds, used for independent provider workers');w.add_argument('--market',choices=['TWSE','TPEx'])
    e=sub.add_parser('queue-events');e.add_argument('--through',required=True)
    u=sub.add_parser('update');u.add_argument('--date',default=str(datetime.now(ZoneInfo('Asia/Taipei')).date()-timedelta(days=1)))
    b=sub.add_parser('backup');b.add_argument('destination')
    args=p.parse_args()
    if args.command in ('audit','compute'):print(enqueue(args.command,{}))
    elif args.command=='queue-repairs':
        from .official_repair import queue_repairs
        print(encode(queue_repairs(limit=args.limit)))
    elif args.command=='update':
        for market in ('TWSE','TPEx'):print(enqueue('official_day',{'market':market,'date':args.date,'quotes':True},f'daily:{market}:{args.date}',priority=0))
    elif args.command=='backup':print(encode(backup(args.destination)))
    elif args.command=='queue-events':
        print(encode(queue_events(args.through)))
    elif args.command=='worker':
        while True:
            if not args.drain:schedule()
            ran=run_one(args.kinds.split(',') if args.kinds else None,args.market)
            if args.once or (args.drain and not ran):break
            if not ran:time.sleep(10)
if __name__=='__main__':main()
