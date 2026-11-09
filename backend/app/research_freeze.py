"""Freeze a repeatable-read warehouse view and rebuild segmented research costs.

The gzip file contains the selected raw facts, provenance, events, calendar and
derived estimates.  It is retrospective: no publication timestamp is invented.
"""
from collections import Counter
from datetime import date
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import gzip,json,os,tempfile
from sqlalchemy import select,func,update
from sqlalchemy.orm import Session
from .db import engine,ProviderResponse
from . import warehouse_schema as s
from .warehouse import encode,insert_ignore,now
from .calculation import load_actions,Event,DIVIDEND,DIVIDEND_RESULT,SPLIT,REDUCTION
from .factor_data import segmented_series
from .warehouse_quality import expected_dates

ROOT=Path(__file__).resolve().parents[2]
MODEL='frozen-close-principal-0.6-v2'
DATASETS=(DIVIDEND,DIVIDEND_RESULT,SPLIT,REDUCTION)

def code_hash():
    h=sha256()
    for name in ('research_freeze.py','factor_data.py','calculation.py','engine.py'):
        p=Path(__file__).with_name(name);h.update(name.encode());h.update(p.read_bytes())
    return h.hexdigest()

def selected_rows(conn,table,sid,start,end):
    key='close' if table is s.prices else 'balance';chosen={}
    q=select(table).where(table.c.security_id==sid,table.c.is_current==True,
        table.c.trading_date>=start,table.c.trading_date<=end).order_by(table.c.trading_date,table.c.observed_at,table.c.id)
    for raw in conn.execute(q).mappings():
        row=dict(raw);day=row['trading_date'];previous=chosen.get(day)
        if row[key] is None:continue
        if previous is None or row['provider']=='FinMind':chosen[day]=row
    return chosen

def _action_data(conn,sec,start,end,days):
    if not sec['identity_key'].startswith('current:'):
        # Code reuse makes the legacy code-only event loader unsafe here.
        return {},['delisted_identity_and_action_coverage_unverified'],[],None
    with Session(bind=conn,join_transaction_mode='create_savepoint') as db:
        responses=[]
        for dataset in DATASETS:
            r=db.execute(select(ProviderResponse.id,ProviderResponse.end_date,ProviderResponse.sha256)
                .where(ProviderResponse.code==sec['code'],ProviderResponse.dataset==dataset,
                    ProviderResponse.start_date<=start,ProviderResponse.http_status==200)
                .order_by(ProviderResponse.end_date.desc(),ProviderResponse.id.desc()).limit(1)).first()
            responses.append({'dataset':dataset,'id':r.id if r else None,'end':r.end_date if r else None,'sha256':r.sha256 if r else None})
        covered=min((x['end'] for x in responses),default=None) if all(x['end'] for x in responses) else None
        warnings=[];events={}
        if covered and covered>=start:
            covered=min(end,covered)
            # Record the exact responses that load_actions will select, not just
            # the response with the greatest coverage end.
            for item in responses:
                r=db.execute(select(ProviderResponse.id,ProviderResponse.end_date,ProviderResponse.sha256).where(
                    ProviderResponse.code==sec['code'],ProviderResponse.dataset==item['dataset'],
                    ProviderResponse.start_date<=start,ProviderResponse.end_date>=covered,
                    ProviderResponse.http_status==200).order_by(ProviderResponse.id.desc()).limit(1)).one()
                item.update(id=r.id,end=r.end_date,sha256=r.sha256)
            try:events,warnings=load_actions(db,sec['code'],start,covered)
            except ValueError as exc:
                warnings=['invalid_corporate_action_source:'+str(exc)];covered=None
        else:warnings=['corporate_action_data_incomplete']
    # The four legacy datasets do not cover par-value changes.  Do not infer a
    # share multiplier from a reference-price ratio; stop the cost segment and
    # preserve the independent source record until verified terms are available.
    extra=conn.execute(select(s.actions).where(s.actions.c.security_id==sec['id'],
        s.actions.c.kind=='par_value_change',s.actions.c.effective_date>=start,s.actions.c.effective_date<=end)
        .order_by(s.actions.c.effective_date,s.actions.c.id)).mappings().all()
    for item in extra:
        day=item['effective_date'];events[day]=Event(day,None,'unhandled_par_value_change',
            'warehouse corporate_actions:'+str(item['id']),'par-value terms and share multiplier require verification')
        responses.append({'dataset':'warehouse_par_value_change','id':item['id'],'capture_id':item['capture_id'],
            'event_key':item['event_key'],'effective_date':day,'terms_json':item['terms_json']})
    if extra:warnings=warnings+['par_value_events_require_verification']
    for day in days:
        if covered is None or day>covered:
            events[day]=Event(day,None,'corporate_action_coverage_gap','warehouse','event coverage not established')
    return events,warnings,responses,covered

def freeze(start=date(2021,10,1),end=None,target_engine=engine,progress=None):
    root=ROOT/'research'/'snapshots';root.mkdir(parents=True,exist_ok=True)
    isolation='REPEATABLE READ' if target_engine.dialect.name=='postgresql' else 'SERIALIZABLE'
    fd,tmp=tempfile.mkstemp(prefix='.freezing-',suffix='.jsonl.gz',dir=root);os.close(fd)
    h=sha256();counts=Counter();cohort_counts={};elig=[]
    try:
        with target_engine.connect().execution_options(isolation_level=isolation) as c, gzip.open(tmp,'wt',encoding='utf8',newline='\n') as out:
            def emit(obj):
                line=encode(obj)+'\n';h.update(line.encode('utf8'));out.write(line)
            if end is None:
                maxima=[c.scalar(select(func.max(s.calendar.c.trading_date)).where(s.calendar.c.market==m,s.calendar.c.is_open==True)) for m in ('TWSE','TPEx')]
                if not all(maxima):raise ValueError('both market calendars are required')
                end=min(maxima)
            if end<start:raise ValueError('end precedes start')
            calendars={m:sorted(set(c.scalars(select(s.calendar.c.trading_date).where(s.calendar.c.market==m,
                s.calendar.c.is_open==True,s.calendar.c.trading_date>=start,s.calendar.c.trading_date<=end)))) for m in ('TWSE','TPEx')}
            index={}
            for m in calendars:
                index[m]={}
                for x in c.execute(select(s.indices).where(s.indices.c.market==m,s.indices.c.trading_date>=start,
                    s.indices.c.trading_date<=end).order_by(s.indices.c.trading_date,s.indices.c.id)).mappings():
                    index[m][str(x['trading_date'])]={'close':x['close'],'capture_id':x['capture_id']}
            securities=c.execute(select(s.securities,s.listings.c.market,s.listings.c.valid_from,s.listings.c.valid_to,s.listings.c.verified,
                s.universes.c.name.label('cohort'),s.universes.c.id.label('universe_id'))
                .join(s.members,s.members.c.security_id==s.securities.c.id).join(s.universes,s.universes.c.id==s.members.c.universe_id)
                .join(s.listings,s.listings.c.security_id==s.securities.c.id).order_by(s.securities.c.id)).mappings().all()
            identities = [(row['cohort'], row['id']) for row in securities]
            if len(set(identities)) != len(identities) or len({sid for _, sid in identities}) != len(identities):
                raise ValueError('ambiguous listing or universe membership; resolve historical market intervals before freezing')
            emit({'type':'header','format':2,'model':MODEL,'source_code_sha256':code_hash(),'start':start,'end':end,
                'dividend_policy':'cash_entitlement_separate_no_automatic_principal_repayment',
                'calendar':calendars,'indices':index,'finance_ratio':'0.6','price_basis':'close',
                'limitations':['retrospective_not_point_in_time','historical_security_identity_unverified',
                               'market_calibration_unavailable','corporate_action_coverage_incomplete']})
            for number,entry in enumerate(securities,1):
                sec=dict(entry);sid=sec['id'];market=sec['market'];begin=start if sec['cohort']=='top500' else date(2015,1,1)
                # Existing benchmark calendars before the requested research start do not form this study's window.
                begin=max(begin,start)
                listing_days=expected_dates(calendars[market],begin,end,sec)
                p=selected_rows(c,s.prices,sid,begin,end);m=selected_rows(c,s.margins,sid,begin,end)
                if sec['verified']:
                    listed=set(listing_days)
                    p={d:v for d,v in p.items() if d in listed}
                    m={d:v for d,v in m.items() if d in listed}
                days=sorted(set(p)|set(m));events,warnings,responses,covered=_action_data(c,sec,begin,end,days)
                if sec['cohort']=='delisted':
                    for day in days:events[day]=Event(day,None,'delisted_action_coverage_unverified','warehouse','identity and event history unverified')
                rows=[]
                for day in days:
                    pr=p.get(day,{});mr=m.get(day,{})
                    values={'trading_date':day,'open':pr.get('open'),'high':pr.get('high'),'low':pr.get('low'),
                        'close':pr.get('close'),'volume':pr.get('volume_shares'),'turnover':pr.get('turnover_ntd'),'limit':mr.get('margin_limit')}
                    values.update({k:mr.get(k) for k in ('margin_buy','margin_sell','repayment','previous_balance','balance')})
                    rows.append(SimpleNamespace(**values))
                values,breaks=segmented_series(rows,events,set(calendars[market]),keep_cash_out_of_principal=True)
                model={v['date']:v for v in values};breakmap={b['date']:b['kind'] for b in breaks}
                quotes=[]
                for day in days:
                    pr=p.get(day);mr=m.get(day);v=model.get(day)
                    quotes.append({'date':day,'price':{k:pr[k] for k in ('id','capture_id','value_sha256','provider','basis','open','high','low','close','volume_shares','turnover_ntd','source_unit','unit_multiplier')} if pr else None,
                        'margin':{k:mr[k] for k in ('id','capture_id','value_sha256','provider','basis','previous_balance','margin_buy','margin_sell','repayment','balance','margin_limit','source_unit','unit_multiplier')} if mr else None,
                        'model':v})
                actions=[{'date':d,'kind':e.kind,'source':e.source,'reason':e.reason,
                    'cash':e.action.cash_distribution if e.action else None,'multiplier':e.action.share_multiplier if e.action else None}
                    for d,e in sorted(events.items()) if e.kind not in ('corporate_action_coverage_gap','delisted_action_coverage_unverified')]
                states=[dict(row) for row in c.execute(select(s.states).where(s.states.c.security_id==sid,
                    s.states.c.valid_from<=end,(s.states.c.valid_to==None)|(s.states.c.valid_to>=begin))
                    .order_by(s.states.c.id)).mappings()]
                suspension=[x for x in states if x['kind']=='suspension' and x['verified']]
                daily=[]
                cohort=cohort_counts.setdefault(sec['cohort'],Counter())
                for day in listing_days:
                    pr=p.get(day);mr=m.get(day);v=model.get(day)
                    suspended=any(x['valid_from']<=day and (x['valid_to'] is None or x['valid_to']>=day) for x in suspension)
                    ps='not_applicable' if suspended else ('available' if pr else 'needs_review')
                    ms='available' if mr else ('not_applicable' if suspended else 'needs_review')
                    reasons=[]
                    if ps!='available':reasons.append('price_'+ps)
                    if ms!='available':reasons.append('margin_'+ms)
                    if day in breakmap:reasons.append(breakmap[day])
                    if not v or v['raw_maintenance'] is None:reasons.append('no_valid_maintenance')
                    if v and v['warmup']:reasons.append('warmup')
                    if not sec['verified']:reasons.append('historical_listing_unverified')
                    reasons.append('publication_time_unverified')
                    if covered is None or day>covered:reasons.append('action_coverage_unverified')
                    valid=bool(ps=='available' and ms=='available' and v and v['raw_maintenance'] is not None and covered and day<=covered)
                    strict=bool(valid and not v['warmup'])
                    exploratory=bool(valid and v['initial_cost_sensitivity_pct']<=5)
                    segment=sha256(encode([sid,v['segment']]).encode()).hexdigest() if v else None
                    daily.append({'security_id':sid,'trading_date':day,'segment_id':segment,'price_status':ps,'margin_status':ms,
                        'strict':strict,'exploratory':exploratory,'reasons_json':encode(sorted(set(reasons)))})
                    cohort['days']+=1;cohort['strict_days']+=strict;cohort['exploratory_days']+=exploratory
                    counts['days']+=1;counts['strict']+=strict;counts['exploratory']+=exploratory
                    if ps=='needs_review' or ms=='needs_review':counts['missing_or_unknown']+=1
                elig.extend(daily)
                emit({'type':'security','security':sec,'action_responses':responses,'action_coverage_end':covered,
                    'warnings':warnings,'actions':actions,'breaks':breaks,'days':quotes,
                    'state_evidence':states,'eligibility':daily})
                counts['stocks']+=1
                if progress and number%20==0:progress({'frozen_stocks':number,'target':len(securities)})
                if number%50==0:print('frozen',number,'of',len(securities),flush=True)
        version=h.hexdigest();path=root/(version+'.jsonl.gz')
        if path.exists():os.remove(tmp)
        else:os.replace(tmp,path)
        manifest={'counts':dict(counts),'eligibility_by_cohort':cohort_counts,'model':MODEL,'code_sha256':code_hash(),'content_sha256':version,
            'eligibility_complete':False,'source_availability':'retrospective_unverified'}
        with target_engine.begin() as c:
            insert_ignore(c,s.research_snapshots,dict(id=version,data_version=version,start_date=start,end_date=end,
                path=str(path.relative_to(ROOT)),manifest_json=encode(manifest),created_at=now()),['id'])
        for offset in range(0,len(elig),1000):
            batch=[x|{'snapshot_id':version} for x in elig[offset:offset+1000]]
            with target_engine.begin() as c:
                if c.dialect.name=='postgresql':from sqlalchemy.dialects.postgresql import insert
                else:from sqlalchemy.dialects.sqlite import insert
                c.execute(insert(s.eligibility).values(batch).on_conflict_do_nothing(index_elements=['snapshot_id','security_id','trading_date']))
        manifest['eligibility_complete']=True
        with target_engine.begin() as c:c.execute(update(s.research_snapshots).where(s.research_snapshots.c.id==version).values(manifest_json=encode(manifest)))
        return {'snapshot':version,'start':str(start),'end':str(end),**manifest}
    finally:
        if os.path.exists(tmp):os.remove(tmp)

def read_snapshot(snapshot_id,target_engine=engine):
    with target_engine.connect() as c:row=c.execute(select(s.research_snapshots).where(s.research_snapshots.c.id==snapshot_id)).mappings().first()
    if not row:raise ValueError('snapshot not found')
    manifest=json.loads(row['manifest_json'])
    if not manifest.get('eligibility_complete'):raise ValueError('snapshot eligibility incomplete')
    path=ROOT/row['path'];h=sha256();records=[]
    with gzip.open(path,'rt',encoding='utf8') as f:
        for line in f:h.update(line.encode());records.append(json.loads(line))
    if h.hexdigest()!=snapshot_id:raise ValueError('snapshot content hash mismatch')
    return records
