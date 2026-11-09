"""Export only public, aggregate results; never publish raw data or local paths."""
from datetime import datetime,timezone
import argparse,base64,gzip,json
from .research_runner import ROOT

def export_public(run_id):
    from sqlalchemy import select
    from .db import engine
    from . import warehouse_schema as s
    with engine.connect() as c:
        state=c.scalar(select(s.runs.c.status).where(s.runs.c.id==run_id))
    if state!='completed':raise ValueError('database research status is not completed')
    source=ROOT/'research'/'runs'/run_id/'report.json'
    report=json.loads(source.read_text(encoding='utf8'))
    if report['status']!='completed' or report['completed_tests']!=800:raise ValueError('only completed 800-cell reports may be published')
    items=[]
    for row in report['results']:
        inf=row['inference'];summary=row['summary']
        items.append({k:row[k] for k in ('quality','threshold','holding_days','trend','candidate','bh_q','by_q')}|
            {'summary':{k:summary[k] for k in ('signals','matched','signal_dates','mean_signal_net_pct',
                 'mean_excess_pp','attrition','years_descriptive','pending','mature','mature_unpaired_pct',
                 'observable_signals','mean_observed_signal_net_pct','mean_control_net_pct',
                 'months_descriptive','matched_feature_balance') if k in summary},
             'inference':{k:inf.get(k) for k in ('status','reason','ci95_pp','p_value','t_stat','block_days','draws_requested')}
                 |{'block_sensitivity':{k:{'status':v['status'],'ci95_pp':v['ci95_pp']}
                     for k,v in inf.get('block_sensitivity',{}).items()}}})
    result={'run_id':report['run_id'],'snapshot':report['snapshot'],'status':report['status'],
        'generated_at_utc':datetime.now(timezone.utc).isoformat(),'study_created_at':report['created_at'],
        'model':report.get('model'),'study_end':report.get('study_end'),
        'planned_tests':report['planned_tests'],'completed_tests':report['completed_tests'],
        'tested':report['tested'],'candidates':report['candidates'],'eligibility':report['eligibility'],
        'limitations':report['limitations'],'results':items}
    target=ROOT/'github-pages'/'research-results.json'
    payload=json.dumps(result,ensure_ascii=False,separators=(',',':')).encode('utf8')
    target.write_bytes(payload)
    # Publish the aggregate separately from the page, using a versioned asset.
    asset=target.with_name(f'research-results-{run_id[:12]}.txt')
    asset.write_bytes(base64.b64encode(gzip.compress(payload,mtime=0)))
    return {'file':str(target.relative_to(ROOT)),'asset':str(asset.relative_to(ROOT)),
        'items':len(items),'run_id':run_id}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-id',required=True);a=p.parse_args();print(json.dumps(export_public(a.run_id)))
