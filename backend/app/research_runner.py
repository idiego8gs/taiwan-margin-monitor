"""Resumable local 800-cell study over one immutable research snapshot."""
from concurrent.futures import ProcessPoolExecutor,as_completed
from hashlib import sha256
from pathlib import Path
from threading import Event,Thread
import gzip,json,os
from sqlalchemy import select,update,text
from .db import engine
from . import warehouse_schema as s
from .warehouse import encode,digest,insert_ignore,now
from .research_freeze import read_snapshot,ROOT
from .research_backtest import (THRESHOLDS,HORIZONS,MODES,GROUPS,CALIPERS,
    build_features,choose_pairs,events_for_horizon,summarize,bootstrap_with_sensitivity)
from .factor_statistics import adjust_pvalues

PLANNED=len(THRESHOLDS)*len(HORIZONS)*len(MODES)*len(GROUPS)

def runner_code_hash():
    h=sha256()
    for name in ('research_runner.py','research_backtest.py','factor_validation.py','factor_statistics.py'):
        p=Path(__file__).with_name(name);h.update(name.encode());h.update(p.read_bytes())
    return h.hexdigest()

def protocol(snapshot,draws):
    return {'snapshot':snapshot,'thresholds':THRESHOLDS,'holding_days':HORIZONS,'quality':MODES,'trend':GROUPS,
        'calipers':CALIPERS,'block_days':20,'draws':draws,'minimum_signal_dates':30,'planned_tests':PLANNED,
        'bootstrap':'circular_centered_two_sided','descriptive_block_sensitivity':[40,60],
        'control_policy':'same_date_market_and_trend_no_new_crossing',
        'trend_policy':'filter_before_independent_holding_gate',
        'fee_each_side':0.001425,'sell_tax':0.003,'slippage_each_side':0.001,'entry':'next_open',
        'exit':'hth_close','code_sha256':runner_code_hash(),'primary_adjustment':'BY',
        'publication_availability':'unverified_retrospective','production_draws':draws==200000}

def _key(mode,threshold,horizon,group):return f'{mode}:{threshold}:{horizon:02d}:{group}'

def _progress(run_id,progress=None,error=None,target_engine=engine):
    with target_engine.begin() as c:
        row=c.execute(select(s.executions).where(s.executions.c.run_id==run_id)).mappings().first()
        if row:
            values={'heartbeat_at':now(),'updated_at':now()}
            if progress is not None:values['progress_json']=encode(progress)
            if error is not None:values['last_error']=error
            c.execute(update(s.executions).where(s.executions.c.run_id==run_id).values(**values))

def _pairs_file(run_id,mode,threshold,horizon,group,events):
    folder=ROOT/'research'/'runs'/run_id/'pairs';folder.mkdir(parents=True,exist_ok=True)
    path=folder/f'{mode}-{threshold}-{horizon:02d}-{group}.jsonl.gz';tmp=path.with_suffix('.tmp')
    with gzip.open(tmp,'wt',encoding='utf8',newline='\n') as f:
        for item in events:f.write(encode(item)+'\n')
    os.replace(tmp,path)
    return str(path.relative_to(ROOT))

def _initialize(snapshot,draws,target_engine):
    p=protocol(snapshot,draws);rid=digest(p)
    with target_engine.begin() as c:
        snap=c.execute(select(s.research_snapshots).where(s.research_snapshots.c.id==snapshot)).mappings().first()
        if not snap or not json.loads(snap['manifest_json']).get('eligibility_complete'):raise ValueError('frozen snapshot unavailable or incomplete')
        if 'eligibility_by_cohort' not in json.loads(snap['manifest_json']):
            raise ValueError('freeze a format 2 snapshot with immutable eligibility before running the corrected protocol')
        insert_ignore(c,s.runs,dict(id=rid,data_version=snapshot,protocol_json=encode(p),status='queued',created_at=now()),['id'])
        insert_ignore(c,s.executions,dict(run_id=rid,progress_json=encode({'completed':0,'total':PLANNED}),updated_at=now()),['run_id'])
    return rid,p

def run(snapshot,workers=4,draws=200000,target_engine=engine):
    if not 1<=workers<=4:raise ValueError('workers must be between 1 and 4')
    if draws<500:raise ValueError('at least 500 draws required')
    rid,_=_initialize(snapshot,draws,target_engine)
    return resume(rid,workers,target_engine)

def resume(run_id,workers=4,target_engine=engine):
    if not 1<=workers<=4:raise ValueError('workers must be between 1 and 4')
    with target_engine.connect() as c:row=c.execute(select(s.runs).where(s.runs.c.id==run_id)).mappings().first()
    if not row:raise ValueError('research run not found')
    if row['status']=='invalidated':raise ValueError('invalidated research cannot be resumed; freeze corrected inputs and start a new run')
    p=json.loads(row['protocol_json'])
    if p.get('code_sha256')!=runner_code_hash():raise ValueError('research code changed; create a new run for reproducibility')
    lock=target_engine.connect();locked=False
    if lock.dialect.name=='postgresql':
        lock_id=int(run_id[:15],16)
        locked=bool(lock.execute(text('SELECT pg_try_advisory_lock(:n)'),{'n':lock_id}).scalar())
        if not locked:lock.close();raise RuntimeError('this research run is already active')
    stop=Event()
    def pulse():
        while not stop.wait(30):
            try:_progress(run_id,target_engine=target_engine)
            except Exception:pass
    thread=Thread(target=pulse,daemon=True);thread.start()
    try:
        with target_engine.begin() as c:
            c.execute(update(s.runs).where(s.runs.c.id==run_id).values(status='running'))
            existing={r.result_key:json.loads(r.metrics_json) for r in c.execute(select(s.results.c.result_key,s.results.c.metrics_json).where(s.results.c.run_id==run_id))}
        _progress(run_id,{'stage':'loading_snapshot','completed':len(existing),'total':PLANNED},target_engine=target_engine)
        records=read_snapshot(p['snapshot'],target_engine);header,by_day,features=build_features(records)
        calendar=sorted(set(header['calendar']['TWSE'])|set(header['calendar']['TPEx']))
        pending={};metadata={};count=len(existing)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for mode in MODES:
                for threshold in THRESHOLDS:
                    pairs=choose_pairs(by_day,mode,threshold)
                    for horizon in HORIZONS:
                        keys=[_key(mode,threshold,horizon,g) for g in GROUPS]
                        if all(k in existing for k in keys):continue
                        for group,key in zip(GROUPS,keys):
                            if key in existing:continue
                            events=events_for_horizon(pairs,horizon,header,group)
                            path=_pairs_file(run_id,mode,threshold,horizon,group,events)
                            summary,matched=summarize(events,group)
                            metadata[key]={'quality':mode,'threshold':threshold,'holding_days':horizon,'trend':group,
                                'summary':summary,'pairs_file':path}
                            future=pool.submit(bootstrap_with_sensitivity,(run_id+':'+key,calendar,matched,p['draws']))
                            pending[future]=key
                    _progress(run_id,{'stage':'matching','completed':count,'total':PLANNED,'quality':mode,
                        'threshold':threshold,'features':features},target_engine=target_engine)
            for future in as_completed(pending):
                key=pending[future];_,inference=future.result();result=metadata[key]|{'inference':inference,'bh_q':None,'by_q':None,'candidate':False}
                with target_engine.begin() as c:
                    insert_ignore(c,s.results,dict(run_id=run_id,result_key=key,metrics_json=encode(result)),['run_id','result_key'])
                existing[key]=result;count+=1
                if count%10==0 or count==PLANNED:
                    _progress(run_id,{'stage':'bootstrap','completed':count,'total':PLANNED,'features':features},target_engine=target_engine)
                    print('research',run_id[:12],count,'/',PLANNED,flush=True)
        if len(existing)!=PLANNED:raise RuntimeError('incomplete hypothesis family')
        keys=sorted(existing);pvalues=[existing[k]['inference'].get('p_value') for k in keys]
        bh=adjust_pvalues(pvalues,method='BH',planned_tests=PLANNED);by=adjust_pvalues(pvalues,method='BY',planned_tests=PLANNED)
        with target_engine.begin() as c:
            for key,b,y in zip(keys,bh,by):
                r=existing[key];r['bh_q']=b;r['by_q']=y;ci=r['inference'].get('ci95_pp');summary=r['summary']
                r['candidate']=bool(p['production_draws'] and y is not None and y<.05 and ci and ci[0]>0 and
                    summary['mean_observed_signal_net_pct'] is not None and summary['mean_observed_signal_net_pct']>0
                    and summary['mean_signal_net_pct']>0 and summary['mean_excess_pp']>0)
                c.execute(update(s.results).where(s.results.c.run_id==run_id,s.results.c.result_key==key).values(metrics_json=encode(r)))
            c.execute(update(s.runs).where(s.runs.c.id==run_id).values(status='completed'))
        _progress(run_id,{'stage':'completed','completed':PLANNED,'total':PLANNED,'features':features},target_engine=target_engine)
        return export(run_id,target_engine)
    except Exception as exc:
        with target_engine.begin() as c:c.execute(update(s.runs).where(s.runs.c.id==run_id).values(status='failed'))
        _progress(run_id,error=f'{type(exc).__name__}: {exc}',target_engine=target_engine)
        raise
    finally:
        stop.set();thread.join(timeout=1)
        if locked:lock.execute(text('SELECT pg_advisory_unlock(:n)'),{'n':lock_id})
        lock.close()

def export(run_id,target_engine=engine):
    with target_engine.connect() as c:
        runrow=c.execute(select(s.runs).where(s.runs.c.id==run_id)).mappings().first()
        if not runrow:raise ValueError('research run not found')
        execution=c.execute(select(s.executions).where(s.executions.c.run_id==run_id)).mappings().first()
        rows=c.execute(select(s.results.c.result_key,s.results.c.metrics_json).where(s.results.c.run_id==run_id).order_by(s.results.c.result_key)).all()
        snapshot=c.execute(select(s.research_snapshots).where(
            s.research_snapshots.c.id==runrow['data_version'])).mappings().one()
    manifest=json.loads(snapshot['manifest_json'])
    report_path=ROOT/'research'/'runs'/run_id/'report.json'
    if 'eligibility_by_cohort' not in manifest and report_path.exists():
        archived=json.loads(report_path.read_text(encoding='utf8'))
        return {'run_id':run_id,'snapshot':runrow['data_version'],'status':runrow['status'],
            'completed':archived['completed_tests'],'planned':archived['planned_tests'],
            'tested':archived['tested'],'candidates':archived['candidates'],
            'report':str(report_path.relative_to(ROOT))}
    results=[json.loads(x.metrics_json) for x in rows]
    report={'run_id':run_id,'snapshot':runrow['data_version'],'status':runrow['status'],'created_at':runrow['created_at'],
        'protocol':json.loads(runrow['protocol_json']),'progress':json.loads(execution['progress_json']) if execution else {},
        'last_error':execution['last_error'] if execution else None,'planned_tests':PLANNED,'completed_tests':len(results),
        'tested':sum(x['inference']['status']=='ok' for x in results),'candidates':sum(x['candidate'] for x in results),
        'model':manifest['model'],'study_start':snapshot['start_date'],'study_end':snapshot['end_date'],
        'eligibility':manifest.get('eligibility_by_cohort',{}),
        'limitations':['retrospective data and current top-500 have survivorship and availability bias; not true PIT/OOS',
            '30 signal dates are not 30 independent observations','industry, historical size, beta and market impact are not controlled',
            'limit-up/limit-down fills are not modelled','unknown outcomes are excluded after matching and can bias results',
            '5% warmup sensitivity is not an error confidence interval','delisted identity and action history remains unverified',
            'previous wider experimentation is not included in this multiple-testing family'],
        'results':results}
    report_path.parent.mkdir(parents=True,exist_ok=True)
    temporary=report_path.with_suffix('.tmp')
    temporary.write_text(encode(report),encoding='utf8')
    os.replace(temporary,report_path)
    return {'run_id':run_id,'snapshot':runrow['data_version'],'status':runrow['status'],'completed':len(results),
        'planned':PLANNED,'tested':report['tested'],'candidates':report['candidates'],
        'report':str(report_path.relative_to(ROOT))}
