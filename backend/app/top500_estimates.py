"""Export bounded, quality-labelled research scenarios, never a verified ranking."""
from datetime import date,datetime,timezone
from decimal import Decimal
from pathlib import Path
import argparse,json,os
from sqlalchemy import select
from .calculation import calculate
from .db import Session,Observation
from .engine import DataQualityError

def build(db,universe,*,start_at,as_of):
    items=[]
    for stock in universe['items']:
        row={'rank':stock['rank'],'code':stock['code'],'name':stock['name'],'market':stock['market'],
             'status':'missing','close':None,'estimated_cost':None,'maintenance_pct':None,
             'balance':None,'warmup':None,'quality_flags':[],'blocked':None}
        try:
            r=calculate(db,stock['code'],start_at=start_at,as_of=as_of,buy_price_method='close')
            if r is not None:
                row['quality_flags']=r['quality_flags'];row['blocked']=r['blocked']
                latest=r['latest']
                if r['first_observation']!=start_at:
                    row['status']='blocked';row['blocked']={'kind':'insufficient_history_for_start','first_observation':r['first_observation']}
                elif not latest or r['blocked'] or latest['date']!=as_of or not latest['raw_maintenance']:
                    row['status']='blocked'
                    if not row['blocked']: row['blocked']={'kind':'missing_as_of_estimate'}
                else:
                    official=db.scalars(select(Observation).where(Observation.code==stock['code'],
                                       Observation.trading_date==as_of,Observation.market==stock['market'])).all()
                    fields=('close','balance','previous_balance','margin_buy','margin_sell','repayment')
                    mismatch=[x for x in fields if official and (getattr(official[0],x) is None or latest[x] is None or getattr(official[0],x)!=latest[x])]
                    if len(official)>1 or mismatch:
                        row['status']='blocked';row['blocked']={'kind':'official_source_conflict','fields':mismatch}
                    else:
                        if not official: row['quality_flags'].append('official_as_of_not_verified')
                        row.update(status='provisional',close=latest['close'],estimated_cost=latest['estimated_cost'],
                                   maintenance_pct=latest['raw_maintenance'],balance=latest['balance'],warmup=latest['warmup'])
        except (DataQualityError,ValueError) as exc:
            row['status']='blocked';row['blocked']={'kind':'invalid_input','detail':str(exc)}
        items.append(row)
    return {'as_of':as_of,'start_at':start_at,'finance_ratio':Decimal('.6'),'buy_price_method':'close',
            'generated_at':datetime.now(timezone.utc),'items':items,
            'summary':{'selected':len(items),'eligible':sum(x['status']=='provisional' for x in items),
                       'blocked':sum(x['status']=='blocked' for x in items),'missing':sum(x['status']=='missing' for x in items)},
            'limitations':['研究情境，未依上市或上櫃市場融資總額校準',
                           '起算日已有餘額以當日收盤價暖機，且假設融資成數60%',
                           '公司行動尚未完整核對，部分成本可能失真；不可用來推斷實際追繳',
                           '2026-11-03當前市值近似名單不代表歷史成分，且部分歷史資料事後修訂']}

def serialize(value):
    if isinstance(value,(date,datetime)): return value.isoformat()
    if isinstance(value,Decimal): return str(value)
    raise TypeError(type(value).__name__)

def main():
    p=argparse.ArgumentParser();p.add_argument('--universe',default='research/top500_20261103/universe.json');p.add_argument('--output',default='research/top500_20261103/estimates.json');p.add_argument('--start',default='2026-07-08');p.add_argument('--as-of',default='2026-11-03');a=p.parse_args()
    universe=json.loads(Path(a.universe).read_text(encoding='utf8'))
    with Session() as db: data=build(db,universe,start_at=date.fromisoformat(a.start),as_of=date.fromisoformat(a.as_of))
    target=Path(a.output);tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(data,default=serialize,ensure_ascii=False,indent=2),encoding='utf8');os.replace(tmp,target)
    print(json.dumps(data['summary'],ensure_ascii=False))
if __name__=='__main__':main()
