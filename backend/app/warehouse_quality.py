"""Audit the warehouse against known security lifetimes; unknown is never zero."""
from collections import Counter
from datetime import date
import argparse,json
from sqlalchemy import select,func,and_,update
from .db import engine
from . import warehouse_schema as s
from .warehouse import issue,upsert,encode,now,digest

def expected_dates(calendar, start, end, listing=None):
    """Bound only by verified listing terms; unknown dates stay in scope."""
    listing=listing or {}
    if listing.get('verified'):
        if listing.get('valid_from'):start=max(start,listing['valid_from'])
        if listing.get('valid_to'):end=min(end,listing['valid_to'])
    return sorted(d for d in calendar if start<=d<=end)

def audit(target_engine=engine,progress=None,as_of=None):
    result={'created_at':now(),'groups':{},'issues':{},'limitations':[
        'Historical security identity, eligibility and publication times remain unverified where evidence is absent.',
        'Complete source responses do not prove complete events, tradability or accurate initial costs.',
        'Historical trading-calendar coverage itself is incomplete in some periods; unknown dates cannot be counted as verified non-trading days.',
        'No historical top-500 market-cap membership or full-market calibration denominator is inferred.']}
    with target_engine.connect() as c:
        universes=c.execute(select(s.universes)).mappings().all()
        for u in universes:
            group=Counter();securities=c.execute(select(s.securities,s.listings.c.market,s.listings.c.valid_from,s.listings.c.valid_to,s.listings.c.verified)
                .join(s.members,s.members.c.security_id==s.securities.c.id).join(s.listings,s.listings.c.security_id==s.securities.c.id)
                .where(s.members.c.universe_id==u['id'])).mappings().all()
            details={}
            for i,sec in enumerate(securities):
                sid=sec['id'];code=sec['code'];group['selected']+=1
                start=date(2021,10,1) if sec['identity_key'].startswith('current:') else date(2015,1,1)
                byp={};bym={}
                for table,byday,field in ((s.prices,byp,'close'),(s.margins,bym,'balance')):
                    for row in c.execute(select(table).where(table.c.security_id==sid,table.c.is_current==True,table.c.trading_date>=start).order_by(table.c.observed_at)).mappings():
                        day=row['trading_date']
                        if day not in byday or (byday[day][field] is None and row[field] is not None) or (row['provider']=='FinMind' and row[field] is not None):byday[day]=row
                p=list(byp.values());m=sorted(bym.values(),key=lambda r:r['trading_date'])
                source_count=c.scalar(select(func.count(func.distinct(s.captures.c.dataset))).where(s.captures.c.code==code,s.captures.c.provider=='FinMind',s.captures.c.dataset.in_(['TaiwanStockPrice','TaiwanStockMarginPurchaseShortSale','TaiwanStockDividend','TaiwanStockDividendResult','TaiwanStockSplitPrice','TaiwanStockCapitalReductionReferencePrice'])))
                group['downloaded']+=source_count==6;group['imported']+=bool(p)
                seen=sorted(set(byp)|set(bym));counts=Counter();previous=None;audit_started=now()
                c.execute(update(s.issues).where(s.issues.c.security_id==sid,s.issues.c.trading_date<start,
                    s.issues.c.kind.in_(['missing_price_or_suspension_unverified','missing_margin_or_eligibility_unverified','previous_balance_conflict']),
                    s.issues.c.status=='needs_review').values(status='not_applicable',evidence_json=encode({'reason':'outside_requested_research_window','research_start':start,'filled':False})))
                # The requested window is independent of observed rows: otherwise
                # missing first/last dates and entirely empty series disappear.
                cal=set(c.scalars(select(s.calendar.c.trading_date).where(s.calendar.c.market==sec['market'],s.calendar.c.is_open==True)))
                market_latest=c.scalar(select(func.max(s.prices.c.trading_date)).where(s.prices.c.market==sec['market'],s.prices.c.is_current==True))
                end=as_of or market_latest
                suspensions=c.execute(select(s.states).where(s.states.c.security_id==sid,s.states.c.kind=='suspension',
                    s.states.c.verified==True)).mappings().all()
                if end:
                    for day in expected_dates(cal,start,end,sec):
                        pr=byp.get(day);mr=bym.get(day)
                        missing=[]
                        if not pr or pr['close'] is None:missing.append('missing_price_or_suspension_unverified')
                        if not mr or mr['balance'] is None:missing.append('missing_margin_or_eligibility_unverified')
                        for kind in missing:
                            # A proven non-trading interval is applicable rather than a quote to fabricate.
                            state=next((x for x in suspensions if x['valid_from']<=day and (x['valid_to'] is None or x['valid_to']>=day)),None)
                            status='not_applicable' if state else 'needs_review'
                            issue(c,f'gap:{sid}:{day}:{kind}',kind,sid=sid,day=day,status=status,
                                evidence={'code':code,'reason':'verified_suspension' if state else 'source_or_security_status_unknown',
                                          'outside_observed_span':not seen or day<seen[0] or day>seen[-1],
                                          'listing_verified':sec['verified'],'state_id':state['id'] if state else None})
                            counts[kind]+=1
                for row in m:
                    day=row['trading_date']
                    if row['identity_valid'] is False:
                        issue(c,f'identity:{sid}:{day}','balance_identity_mismatch',sid=sid,day=day,evidence={k:row[k] for k in ('previous_balance','margin_buy','margin_sell','repayment','balance','capture_id')});counts['balance_identity_mismatch']+=1
                    if previous and previous['balance'] is not None and row['previous_balance'] is not None and row['previous_balance']!=previous['balance']:
                        issue(c,f'continuity:{sid}:{day}','previous_balance_conflict',sid=sid,day=day,evidence={
                            'code':code,'market':sec['market'],'previous_date':previous['trading_date'],
                            'previous_balance':previous['balance'],'reported_previous_balance':row['previous_balance'],
                            'capture_id':row['capture_id'],'reason':'not_inferred_as_trade'})
                        counts['previous_balance_conflict']+=1
                    previous=row
                c.execute(update(s.issues).where(s.issues.c.security_id==sid,s.issues.c.status=='needs_review',
                    s.issues.c.kind.in_(['missing_price_or_suspension_unverified','missing_margin_or_eligibility_unverified','balance_identity_mismatch','previous_balance_conflict']),
                    s.issues.c.last_seen_at<audit_started).values(status='resolved',resolved_at=now()))
                group['price_rows']+=len(p);group['margin_rows']+=len(m)
                group['stocks_without_margin']+=not bool(m)
                group['verified']+=bool(p and m and not counts and source_count==6 and sec['identity_status']=='verified')
                computable=c.scalar(select(func.count()).select_from(s.estimates).where(s.estimates.c.security_id==sid,s.estimates.c.raw_maintenance!=None)) or 0
                group['computable']+=computable>0
                details[code]={'price_rows':len(p),'margin_rows':len(m),'issues':dict(counts),'computed_days':computable,
                    'first_observation':seen[0] if seen else None,'last_observation':seen[-1] if seen else None}
                if progress and i%20==0:progress({'group':u['name'],'checked':i,'target':len(securities)})
                c.commit()
            result['groups'][u['name']]={'summary':dict(group),'stocks':details}
        result['issues']={f'{kind}:{status}':n for kind,status,n in c.execute(select(s.issues.c.kind,s.issues.c.status,func.count()).group_by(s.issues.c.kind,s.issues.c.status))}
        upsert(c,s.checks,dict(name='coverage',summary_json=encode(result),checked_at=now()),['name']);c.commit()
    return result
def main():
    p=argparse.ArgumentParser();p.add_argument('--output',default='research/warehouse_coverage.json');a=p.parse_args()
    result=audit()
    from pathlib import Path
    Path(a.output).write_text(json.dumps(result,ensure_ascii=True,indent=2,default=str),encoding='utf8')
    print(json.dumps({k:v['summary'] for k,v in result['groups'].items()}),flush=True)
if __name__=='__main__':main()
