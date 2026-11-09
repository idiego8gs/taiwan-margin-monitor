"""Produce auditable stock-level calculation examples from a frozen input file."""
from decimal import Decimal
from pathlib import Path
import argparse,gzip,json
from sqlalchemy import select
from .db import engine
from . import warehouse_schema as s
from .research_freeze import ROOT
from .warehouse import encode

def examples(snapshot,target_engine=engine):
    with target_engine.connect() as c:row=c.execute(select(s.research_snapshots).where(s.research_snapshots.c.id==snapshot)).mappings().first()
    if not row:raise ValueError('snapshot not found')
    chosen={'3363':'上詮','2308':'台達電','8039':'台虹','2454':'聯發科'};found={}
    with gzip.open(ROOT/row['path'],'rt',encoding='utf8') as f:
        for line in f:
            item=json.loads(line)
            if item['type']!='security' or item['security']['cohort']!='top500' or item['security']['code'] not in chosen:continue
            code=item['security']['code'];days=item['days']
            for i in range(len(days)-1,-1,-1):
                day=days[i];model=day.get('model');price=day.get('price');margin=day.get('margin')
                if not model or model['raw_maintenance'] is None or not price or not margin:continue
                prev=days[i-1]['model'] if i else None
                previous_cost=prev['estimated_cost'] if prev and prev['segment']==model['segment'] else None
                calculated=Decimal(price['close'])/(Decimal(model['estimated_cost'])*Decimal('0.6'))*100
                difference=calculated-Decimal(model['raw_maintenance'])
                found[code]={'name':chosen[code],'security_id':item['security']['id'],'date':day['date'],
                    'price':price,'margin':margin,'estimated_cost':model['estimated_cost'],'previous_cost_same_segment':previous_cost,
                    'finance_ratio':'0.6','uncalibrated_maintenance':model['raw_maintenance'],'formula_check_difference_pp':difference,
                    'warmup':model['warmup'],'initial_cost_sensitivity_pct':model['initial_cost_sensitivity_pct'],
                    'segment':model['segment'],'events_on_day':[x for x in item['actions'] if x['date']==day['date']],
                    'warnings':item['warnings']+['historical_source_availability_unverified','uncalibrated']}
                break
    target=ROOT/'research'/'snapshots'/(snapshot+'-examples.json')
    target.write_text(encode({'snapshot':snapshot,'stocks':found,'missing':[x for x in chosen if x not in found]}),encoding='utf8')
    return {'path':str(target.relative_to(ROOT)),'stocks':len(found),'missing':[x for x in chosen if x not in found]}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--snapshot',required=True);args=p.parse_args();print(encode(examples(args.snapshot)))
