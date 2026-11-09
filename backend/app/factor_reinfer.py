"""Refine block-bootstrap inference locally on frozen matched outcomes."""
from concurrent.futures import ProcessPoolExecutor,as_completed
from datetime import date,datetime,timezone
from pathlib import Path
import argparse,gzip,hashlib,json,os
from .calculation import load_calendar
from .db import Session
from .factor_statistics import moving_block_inference,adjust_pvalues
from .factor_validation import describe

def task(index,rows,calendar,draws):
    return index,moving_block_inference(rows,calendar,block_days=10,draws=draws)

def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser();p.add_argument('--draws',type=int,default=20000);p.add_argument('--workers',type=int,default=4);a=p.parse_args()
    if not 1<=a.workers<=8:raise ValueError('workers must be 1..8')
    result=json.loads(Path('research/factor_validation.json').read_text(encoding='utf8'))
    with Session() as db:calendar=sorted(load_calendar(db,date(2021,10,1),date(2026,11,3)))
    with gzip.open('research/factor_matches.jsonl.gz','rt',encoding='utf8') as f:
        matches=[json.loads(line) for line in f]
    for x in matches:
        for k in ('signal_date','entry_date','exit_date'):x[k]=date.fromisoformat(x[k])
    jobs=[]
    for i,r in enumerate(result['results']):
        rows=[m for m in matches if m['quality']==r['quality'] and m['threshold']==r['threshold'] and
              m['holding_days']==r['holding_days'] and (r['trend']=='all' or m['trend']==r['trend'])]
        r['summary']=describe(rows)
        r['periods_descriptive']={name:describe([x for x in rows if lo<=x['signal_date'].year<=hi])
            for name,lo,hi in [('2022-2023',2022,2023),('2024',2024,2024),('2025-2026',2025,2026)]}
        jobs.append((i,rows,calendar,a.draws))
    print('tests',len(jobs),'workers',a.workers,'draws',a.draws,flush=True)
    completed=0
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        pending=[pool.submit(task,*job) for job in jobs]
        for future in as_completed(pending):
            index,inf=future.result();result['results'][index]['inference']=inf;completed+=1
            Path('research/factor_inference_progress.json').write_text(json.dumps({'status':'running','completed':completed,'total':len(jobs),'draws_each_test':a.draws,'updated_at':datetime.now(timezone.utc).isoformat()}),encoding='utf8')
    pvals=[r['inference'].get('p_value') for r in result['results']]
    for r,b,y in zip(result['results'],adjust_pvalues(pvals,method='BH'),adjust_pvalues(pvals,method='BY')):r['bh_q']=b;r['by_q']=y
    result['generated_at']=datetime.now(timezone.utc).isoformat()
    result['tested']=sum(r['inference']['status']=='ok' for r in result['results'])
    result['bh_rejections']=sum(r['bh_q'] is not None and r['bh_q']<.05 for r in result['results'])
    result['by_rejections']=sum(r['by_q'] is not None and r['by_q']<.05 for r in result['results'])
    result['provenance_sha256']={p:sha(p) for p in ('research/factor_snapshot.sqlite','research/factor_data/model_rows.jsonl.gz','research/factor_matches.jsonl.gz','research/benchmark/taiex_202110_202611.json','research/benchmark/tpex_202110_202611.json')}
    target=Path('research/factor_validation_final.json');temporary=target.with_suffix('.tmp');temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf8');os.replace(temporary,target)
    Path('research/factor_inference_progress.json').write_text(json.dumps({'status':'completed','completed':completed,'total':len(jobs),'draws_each_test':a.draws,'updated_at':datetime.now(timezone.utc).isoformat()}),encoding='utf8')
    print(json.dumps({'tested':result['tested'],'bh_rejections':result['bh_rejections'],'by_rejections':result['by_rejections'],'output':str(target)}),flush=True)

if __name__=='__main__':main()
