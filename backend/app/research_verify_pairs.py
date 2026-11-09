"""Regenerate every signal/control pair from the frozen snapshot and compare."""
from hashlib import sha256
from pathlib import Path
import argparse,gzip,json
from sqlalchemy import select
from .db import engine
from . import warehouse_schema as s
from .research_runner import runner_code_hash,MODES,THRESHOLDS,HORIZONS,GROUPS
from .research_backtest import build_features,choose_pairs,events_for_horizon
from .research_freeze import ROOT,read_snapshot
from .warehouse import encode

def verify(run_id,target_engine=engine):
    with target_engine.connect() as c:row=c.execute(select(s.runs).where(s.runs.c.id==run_id)).mappings().first()
    if not row:raise ValueError('run not found')
    protocol=json.loads(row['protocol_json'])
    if protocol['code_sha256']!=runner_code_hash():raise ValueError('research code changed')
    header,by_day,_=build_features(read_snapshot(row['data_version'],target_engine));folder=ROOT/'research'/'runs'/run_id/'pairs'
    expected=sha256();actual=sha256();events=files=0;errors=[]
    for mode in MODES:
        for threshold in THRESHOLDS:
            pairs=choose_pairs(by_day,mode,threshold)
            for horizon in HORIZONS:
                for group in GROUPS:
                    name=f'{mode}-{threshold}-{horizon:02d}-{group}.jsonl.gz';path=folder/name
                    if not path.exists():errors.append({'file':name,'reason':'missing'});continue
                    with gzip.open(path,'rt',encoding='utf8') as f:
                        for line in f:expected.update((name+'\t'+line).encode())
                    for value in events_for_horizon(pairs,horizon,header,group):
                        actual.update((name+'\t'+encode(value)+'\n').encode());events+=1
                    files+=1
            print('verified',mode,threshold,flush=True)
    result={'run_id':run_id,'files':files,'events':events,'expected_sha256':expected.hexdigest(),
        'actual_sha256':actual.hexdigest(),'errors':errors}
    result['matches']=not errors and result['expected_sha256']==result['actual_sha256']
    (folder.parent/'signal-replay.json').write_text(encode(result),encoding='utf8')
    if not result['matches']:raise RuntimeError('signal pair replay mismatch')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-id',required=True);args=p.parse_args();print(encode(verify(args.run_id)))
