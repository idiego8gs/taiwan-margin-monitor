"""Independently rebuild every frozen cost series and compare its exact values."""
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import argparse,gzip,json
from sqlalchemy import select
from .db import engine
from . import warehouse_schema as s
from .calculation import Event
from .engine import Action
from .factor_data import segmented_series
from .research_freeze import ROOT,code_hash
from .warehouse import encode

def verify(snapshot,target_engine=engine):
    with target_engine.connect() as c:row=c.execute(select(s.research_snapshots).where(s.research_snapshots.c.id==snapshot)).mappings().first()
    if not row:raise ValueError('snapshot not found')
    filehash=sha256();derived=sha256();stocks=days=mismatches=0;sample=[];header=None
    with gzip.open(ROOT/row['path'],'rt',encoding='utf8') as f:
        for line in f:
            filehash.update(line.encode());data=json.loads(line)
            if data['type']=='header':
                header=data
                if header['source_code_sha256']!=code_hash():raise ValueError('snapshot model code no longer matches')
                continue
            sec=data['security'];calendar={date.fromisoformat(x) for x in header['calendar'][sec['market']]};events={}
            for e in data['actions']:
                day=date.fromisoformat(e['date']);action=Action(share_multiplier=Decimal(e['multiplier']),cash_distribution=Decimal(e['cash'])) if e['multiplier'] is not None else None
                events[day]=Event(day,action,e['kind'],e['source'],e['reason'])
            rows=[]
            for value in data['days']:
                day=date.fromisoformat(value['date']);p=value['price'] or {};m=value['margin'] or {}
                if sec['cohort']=='delisted':events[day]=Event(day,None,'delisted_action_coverage_unverified','warehouse','identity and event history unverified')
                elif not data['action_coverage_end'] or value['date']>data['action_coverage_end']:
                    events[day]=Event(day,None,'corporate_action_coverage_gap','warehouse','event coverage not established')
                values={'trading_date':day,'open':Decimal(p['open']) if p.get('open') is not None else None,
                    'high':Decimal(p['high']) if p.get('high') is not None else None,'low':Decimal(p['low']) if p.get('low') is not None else None,
                    'close':Decimal(p['close']) if p.get('close') is not None else None,'volume':p.get('volume_shares'),
                    'turnover':Decimal(p['turnover_ntd']) if p.get('turnover_ntd') is not None else None,'limit':m.get('margin_limit')}
                values.update({k:m.get(k) for k in ('margin_buy','margin_sell','repayment','previous_balance','balance')})
                rows.append(SimpleNamespace(**values))
            models,_=segmented_series(rows,events,calendar,
                keep_cash_out_of_principal=header.get('format')==2)
            actual={str(v['date']):json.loads(encode(v)) for v in models}
            expected={v['date']:v['model'] for v in data['days'] if v['model'] is not None}
            for day in sorted(set(actual)|set(expected)):
                days+=1;v=encode(actual.get(day));derived.update((str(sec['id'])+'\t'+day+'\t'+v+'\n').encode())
                if actual.get(day)!=expected.get(day):
                    mismatches+=1
                    if len(sample)<10:sample.append({'security_id':sec['id'],'date':day})
            stocks+=1
            if stocks%50==0:print('replayed',stocks,'stocks',flush=True)
    result={'snapshot':snapshot,'snapshot_hash_matches':filehash.hexdigest()==snapshot,'stocks':stocks,
        'model_days':days,'mismatches':mismatches,'examples':sample,'model_output_sha256':derived.hexdigest()}
    path=ROOT/'research'/'snapshots'/(snapshot+'-replay.json');path.write_text(encode(result),encoding='utf8')
    if mismatches or not result['snapshot_hash_matches']:raise RuntimeError('frozen replay mismatch; see '+str(path))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--snapshot',required=True);args=p.parse_args();print(encode(verify(args.snapshot)))
