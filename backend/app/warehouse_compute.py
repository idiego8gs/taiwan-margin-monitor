"""Build immutable, segmented research estimates from explicit source selections."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import argparse,json
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from .db import engine,ProviderResponse
from . import warehouse_schema as s
from .warehouse import encode,digest,insert_ignore,issue,now,upsert
from .calculation import load_actions,build_series
from .factor_data import segmented_series

MODEL='warehouse-close-principal-0.6-v3'
def refresh_counts(target_engine=engine):
    """Refresh computed coverage without pretending a full quality audit just ran."""
    with target_engine.begin() as c:
        snap=c.execute(select(s.snapshots).order_by(s.snapshots.c.created_at.desc()).limit(1)).mappings().first()
        check=c.execute(select(s.checks).where(s.checks.c.name=='coverage')).mappings().first()
        if not snap or not check:return
        model=json.loads(snap['summary_json'])['model_version'];coverage=json.loads(check['summary_json'])
        counts=dict(c.execute(select(s.universes.c.name,func.count(func.distinct(s.estimates.c.security_id)))
            .join(s.members,s.members.c.universe_id==s.universes.c.id).join(s.estimates,s.estimates.c.security_id==s.members.c.security_id)
            .where(s.estimates.c.data_version==snap['data_version'],s.estimates.c.model_version==model,s.estimates.c.raw_maintenance!=None)
            .group_by(s.universes.c.name)).all())
        for name,group in coverage['groups'].items():group['summary']['computable']=counts.get(name,0)
        coverage['computed_data_version']=snap['data_version'];coverage['computed_at']=str(snap['created_at'])
        upsert(c,s.checks,dict(name='coverage',summary_json=encode(coverage),checked_at=check['checked_at']),['name'])
def compute(target_engine=engine,progress=None):
    isolation='REPEATABLE READ' if target_engine.dialect.name=='postgresql' else 'SERIALIZABLE'
    with target_engine.connect() as c,target_engine.connect().execution_options(isolation_level=isolation) as source:
        # A vintage includes both normalized record values and immutable capture hashes.
        h=__import__('hashlib').sha256()
        for table in (s.prices,s.margins):
            for r in source.execute(select(table.c.security_id,table.c.trading_date,table.c.provider,table.c.value_sha256,table.c.capture_id).where(table.c.is_current==True).order_by(table.c.id)):
                h.update((encode(list(r))+'\n').encode())
        for r in source.execute(select(s.actions.c.event_key).order_by(s.actions.c.id)):h.update(r[0].encode())
        for r in source.execute(select(s.captures.c.id,s.captures.c.dataset,s.captures.c.end_date,s.captures.c.sha256).order_by(s.captures.c.id)):
            h.update((encode(list(r))+'\n').encode())
        version=h.hexdigest();summary={'data_version':version,'model_version':MODEL,'stocks':0,'model_days':0,'blocked_stocks':0,'as_of':None,'calibration':'unavailable_scope_not_verified'}
        securities=source.execute(select(s.securities,s.listings.c.market).join(s.listings,s.listings.c.security_id==s.securities.c.id).order_by(s.securities.c.id)).mappings().all()
        calendars={m:set(source.scalars(select(s.calendar.c.trading_date).where(s.calendar.c.market==m,s.calendar.c.is_open==True))) for m in ('TWSE','TPEx')}
        for index,sec in enumerate(securities):
            sid=sec['id'];market=sec['market'];code=sec['code'];p={};m={}
            for table,byday in ((s.prices,p),(s.margins,m)):
                query=select(table).where(table.c.security_id==sid,table.c.is_current==True,table.c.trading_date>=date(2021,10,1)).order_by(table.c.trading_date,table.c.observed_at)
                for row in source.execute(query).mappings():
                    # Preserve the FinMind baseline when present; official observations fill missing values.
                    key='close' if table is s.prices else 'balance'
                    if row[key] is None:continue
                    day=row['trading_date']
                    if day not in byday or row['provider']=='FinMind':byday[day]=row
            days=sorted(set(p)|set(m))
            if not days:continue
            existing=c.scalar(select(func.count()).select_from(s.estimates).where(s.estimates.c.security_id==sid,s.estimates.c.data_version==version,s.estimates.c.model_version==MODEL))
            if existing:
                stored_day=c.scalar(select(func.max(s.estimates.c.trading_date)).where(s.estimates.c.security_id==sid,s.estimates.c.data_version==version,s.estimates.c.model_version==MODEL))
                summary['stocks']+=1;summary['model_days']+=existing;summary['as_of']=max(summary['as_of'] or stored_day,stored_day);continue
            rows=[]
            for day in days:
                pr=p.get(day,{});mr=m.get(day,{})
                row=dict(trading_date=day,open=pr.get('open'),high=pr.get('high'),low=pr.get('low'),close=pr.get('close'),
                    volume=pr.get('volume_shares'),turnover=pr.get('turnover_ntd'),limit=mr.get('margin_limit'))
                row.update({k:mr.get(k) for k in ('margin_buy','margin_sell','repayment','previous_balance','balance')})
                rows.append(SimpleNamespace(**row))
            # Legacy archival loader already has precise, independently checked cash-dividend handling.
            # Delisted actions use their own identity's normalized archives, never a reused current code.
            with Session(bind=source,join_transaction_mode='create_savepoint') as session:
                if sec['identity_key'].startswith('current:'):
                    from .calculation import Event,DIVIDEND,DIVIDEND_RESULT,SPLIT,REDUCTION
                    ends=[session.scalar(select(func.max(ProviderResponse.end_date)).where(ProviderResponse.code==code,
                        ProviderResponse.dataset==dataset,ProviderResponse.start_date<=days[0],ProviderResponse.http_status==200))
                        for dataset in (DIVIDEND,DIVIDEND_RESULT,SPLIT,REDUCTION)]
                    covered=min(ends) if all(ends) else None
                    events,warnings=load_actions(session,code,days[0],min(days[-1],covered)) if covered and covered>=days[0] else ({},['corporate_action_data_incomplete'])
                    for day in days:
                        if covered is None or day>covered:events[day]=Event(day,None,'corporate_action_coverage_gap','warehouse','公司行動來源未涵蓋此日，不能假定沒有事件')
                    # Par-value changes are a separate archived dataset.  Price
                    # ratios alone do not prove the correct share multiplier.
                    for action in source.execute(select(s.actions).where(s.actions.c.security_id==sid,
                            s.actions.c.kind=='par_value_change',s.actions.c.effective_date!=None)).mappings():
                        day=action['effective_date']
                        if day in p or day in m:
                            events[day]=Event(day,None,'unhandled_par_value_change','warehouse corporate_actions:'+str(action['id']),
                                '面額變更條款及股數倍率待核對')
                            warnings=warnings+['par_value_events_require_verification']
                else:events,warnings={},['delisted_corporate_actions_unverified']
            if sec['identity_key'].startswith('delisted:'):
                from .calculation import Event
                for action in source.execute(select(s.actions).where(s.actions.c.security_id==sid,s.actions.c.effective_date!=None)).mappings():
                    day=action['effective_date'];events[day]=Event(day,None,'unverified_delisted_action','warehouse','條款及歷史身分尚未核實')
            values,breaks=segmented_series(rows,events,calendars[market],keep_cash_out_of_principal=True)
            grouped={}
            for value in values:grouped.setdefault(value['segment'],[]).append(value)
            for n,items in grouped.items():
                segment_id=digest([sid,version,MODEL,n]);start=items[0]['date'];end=items[-1]['date']
                before=[b for b in breaks if b['date']<=start];after=[b for b in breaks if b['date']>end]
                insert_ignore(c,s.segments,dict(id=segment_id,security_id=sid,model_version=MODEL,data_version=version,
                    start_date=start,end_date=end,warmup=any(x['warmup'] for x in items),break_json=encode({'before':before[-1] if before else None,'after':after[0] if after else None})),['id'])
                batch=[]
                for v in items:
                    day=v['date'];pr=p.get(day);mr=m.get(day)
                    quality={'warmup':v['warmup'],'initial_cost_sensitivity_pct':v['initial_cost_sensitivity_pct'],
                        'warnings':warnings+['non_point_in_time','finance_ratio_assumed','uncalibrated','share_unit_assumed_1000'],
                        'cash_dividend_per_share':events[day].action.cash_distribution
                            if day in events and events[day].kind=='cash_dividend' and events[day].action else None,
                        'price_capture_id':pr['capture_id'] if pr else None,'margin_capture_id':mr['capture_id'] if mr else None,
                        'price_provider':pr['provider'] if pr else None,'margin_provider':mr['provider'] if mr else None}
                    batch.append(dict(security_id=sid,trading_date=day,model_version=MODEL,data_version=version,
                        segment_id=segment_id,estimated_cost=v['estimated_cost'],raw_maintenance=v['raw_maintenance'],
                        finance_ratio=Decimal('0.6'),calibration_factor=None,quality_json=encode(quality),metrics_json=encode(v)))
                for offset in range(0,len(batch),500):c.execute(s.estimates.insert(),batch[offset:offset+500])
            for b in breaks:issue(c,f"model-break:{version}:{sid}:{b['date']}:{b['kind']}",b['kind'],sid=sid,day=b['date'],evidence=b)
            if values:summary['stocks']+=1;summary['model_days']+=len(values);summary['as_of']=max(summary['as_of'] or values[-1]['date'],values[-1]['date'])
            if breaks:summary['blocked_stocks']+=1
            c.commit()
            if progress and index%10==0:progress({'computed':index+1,'target':len(securities),'data_version':version})
            if (index+1)%25==0:print('computed',index+1,'model days',summary['model_days'],flush=True)
        if summary['as_of']:
            snapshot_id=digest([version,MODEL])
            # Research-only is visible but never promoted to a production-complete snapshot.
            insert_ignore(c,s.snapshots,dict(id=snapshot_id,as_of=summary['as_of'],status='research_only',data_version=version,summary_json=encode(summary)),['id']);c.commit()
    refresh_counts(target_engine)
    return summary
if __name__=='__main__':print(encode(compute()))
