"""Aggregate immutable daily eligibility by cohort, market, year and reason."""
from collections import Counter,defaultdict
from pathlib import Path
import argparse,json,gzip
from sqlalchemy import select
from .db import engine
from . import warehouse_schema as s
from .warehouse import encode
from .research_freeze import ROOT

def report(snapshot,target_engine=engine):
    groups=defaultdict(Counter);reasons=defaultdict(Counter)
    with target_engine.connect() as c:
        snap=c.execute(select(s.research_snapshots).where(s.research_snapshots.c.id==snapshot)).mappings().first()
    if not snap:raise ValueError('snapshot not found')
    identities={}
    with gzip.open(ROOT/snap['path'],'rt',encoding='utf8') as f:
        for line in f:
            row=json.loads(line)
            if row['type']=='security':
                sec=row['security'];identities[sec['id']]=(sec['cohort'],sec['market'])
    q=select(s.eligibility.c.security_id,s.eligibility.c.trading_date,s.eligibility.c.price_status,
        s.eligibility.c.margin_status,s.eligibility.c.strict,s.eligibility.c.exploratory,s.eligibility.c.reasons_json)
    q=q.where(s.eligibility.c.snapshot_id==snapshot)
    with target_engine.connect().execution_options(stream_results=True) as c:
        for sid,day,price,margin,strict,exploratory,why in c.execute(q):
            cohort,market=identities[sid]
            key=(cohort,market,day.year);g=groups[key];g['expected_days']+=1;g['strict_days']+=strict;g['exploratory_days']+=exploratory
            g['price_needs_review']+=price=='needs_review';g['margin_needs_review']+=margin=='needs_review'
            g['price_not_applicable']+=price=='not_applicable';g['margin_not_applicable']+=margin=='not_applicable'
            for reason in json.loads(why):reasons[key][reason]+=1
    if not groups:raise ValueError('snapshot eligibility not found')
    items=[]
    for (cohort,market,year),g in sorted(groups.items()):
        items.append({'cohort':cohort,'market':market,'year':year,**dict(g),
            'strict_excluded_pct':100*(g['expected_days']-g['strict_days'])/g['expected_days'],
            'exploratory_excluded_pct':100*(g['expected_days']-g['exploratory_days'])/g['expected_days'],
            'reasons':dict(reasons[(cohort,market,year)])})
    result={'snapshot':snapshot,'unit':'security-trading-day','items':items,
        'notes':['Unknown listing/eligibility dates remain in scope; counts can include days before an actual listing.',
                 'Reasons overlap and must not be added. A warm-up threshold is not proof of accuracy.',
                 'Retrospective data, historical identity, publication times and other action categories remain unverified.']}
    target=ROOT/'research'/'snapshots'/(snapshot+'-coverage.json');target.write_text(encode(result),encoding='utf8')
    return {'path':str(target.relative_to(ROOT)),'groups':len(items),'snapshot':snapshot}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--snapshot',required=True);args=p.parse_args();print(encode(report(args.snapshot)))
