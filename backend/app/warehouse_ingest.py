"""Import archived evidence into versioned tables without inventing unavailable terms."""
from datetime import date,datetime,timezone
from decimal import Decimal
from pathlib import Path
import argparse,json,hashlib
from sqlalchemy import select,func
from .db import engine,ProviderResponse,RawResponse,ProviderObservation,Observation
from . import warehouse_schema as s
from .warehouse import (record_capture,security_id,issue,encode,digest,insert_ignore,
                         upsert,revision_rows,now)
from .finmind import parse_response,PRICE,MARGIN,EVENTS,request_url,ProviderError
from .market_totals import parse_total

ROOT=Path(__file__).resolve().parents[2]
TOP=ROOT/'research/top500_20261103'
DELISTED=ROOT/'research/delisted_202110_202611'
def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def dt(value):return datetime.fromisoformat(value) if value else None

def import_universes(conn):
    top=read(TOP/'universe.json');dead=read(DELISTED/'universe.json');mapping={}
    for kind,document in [('top500',top),('delisted',dead)]:
        uid=digest(document)
        insert_ignore(conn,s.universes,dict(id=uid,name=kind,as_of=date(2026,11,3),pit_verified=False,definition_json=encode(document)),['id'])
        for item in document['items']:
            code=item['code'];market=item['market'];end=item.get('end_date')
            identity=f'current:{market}:{code}' if kind=='top500' else f'delisted:{market}:{code}:{end}'
            sid=security_id(conn,code,item.get('name',code),identity)
            mapping[(kind,code)]=sid
            insert_ignore(conn,s.members,dict(universe_id=uid,security_id=sid,rank=item.get('rank')),['universe_id','security_id'])
            insert_ignore(conn,s.listings,dict(security_id=sid,market=market,valid_from=None,
                valid_to=date.fromisoformat(end) if end else None,source_key=identity,
                terms_json=encode(item),verified=False),['source_key'])
    return mapping

def legacy_captures(conn):
    for row in conn.execute(select(ProviderResponse.__table__)).mappings():
        record_capture(conn,row['payload'],key=f"legacy:provider:{row['id']}",provider=row['provider'],dataset=row['dataset'],
            code=row['code'],url=row['source_url'],start=row['start_date'],end=row['end_date'],fetched_at=row['fetched_at'],http_status=row['http_status'])
    for row in conn.execute(select(RawResponse.__table__)).mappings():
        market='TWSE' if 'twse.com' in row['source'] else 'TPEx'
        dataset='official_margin' if ('MI_MARGN' in row['source'] or 'margin_bal_result' in row['source']) else 'official_prices'
        cid=record_capture(conn,row['payload'],key=f"legacy:official:{row['id']}",provider=market,dataset=dataset,
            market=market,url=row['source'],start=row['trading_date'],end=row['trading_date'],fetched_at=row['fetched_at'])
        amount=parse_total(row['payload'],market,row['trading_date'])
        if amount:insert_ignore(conn,s.totals,dict(market=market,trading_date=row['trading_date'],capture_id=cid,
            amount_ntd=amount,scope_json=encode({'source_unit':'thousand_ntd','denominator_verified':False})),['market','trading_date','capture_id'])

def event_records(conn,sid,cid,dataset,payload):
    data=json.loads(payload).get('data',[])
    for r in data:
        kind={'TaiwanStockDividend':'dividend_policy','TaiwanStockDividendResult':'ex_right_result',
              'TaiwanStockCapitalReductionReferencePrice':'capital_reduction','TaiwanStockSplitPrice':'split_reference',
              'TaiwanStockParValueChange':'par_value_change'}[dataset]
        value=r.get('CashExDividendTradingDate') or r.get('StockExDividendTradingDate') if dataset=='TaiwanStockDividend' else r.get('date')
        try:effective=date.fromisoformat(value) if value else None
        except ValueError:effective=None
        announcement=None
        try:announcement=datetime.fromisoformat(r['AnnouncementDate']).replace(tzinfo=timezone.utc) if r.get('AnnouncementDate') else None
        except ValueError:pass
        # Date-only announcements are stored as original terms; not precise availability times.
        values=dict(security_id=sid,capture_id=cid,event_key=digest([sid,dataset,cid,r]),kind=kind,
            effective_date=effective,announced_at=None,terms_json=encode(r),verified=False,
            share_multiplier=None,cash_per_share=None)
        insert_ignore(conn,s.actions,values,['event_key'])

def rows_for(sid,market,provider,prices,margins,captures,observed,basis):
    outprice=[];outmargin=[]
    for day,r in prices.items():
        values=dict(open=r['open'],high=r['high'],low=r['low'],close=r['close'],volume_shares=r['volume'],turnover_ntd=r['turnover'])
        outprice.append(dict(security_id=sid,trading_date=day,provider=provider,market=market,basis=basis,
            capture_id=captures[PRICE],value_sha256=digest(values),observed_at=observed[PRICE],is_current=True,
            source_unit='shares',unit_multiplier=Decimal(1),**values))
    for day,r in margins.items():
        values={('margin_limit' if k=='limit' else k):v for k,v in r.items()}
        required=[values.get(k) for k in ('previous_balance','margin_buy','margin_sell','repayment','balance')]
        valid=None if any(v is None for v in required) else values['previous_balance']+values['margin_buy']-values['margin_sell']-values['repayment']==values['balance']
        outmargin.append(dict(security_id=sid,trading_date=day,provider=provider,market=market,basis='margin_trade_units',
            capture_id=captures[MARGIN],value_sha256=digest(values),observed_at=observed[MARGIN],is_current=True,
            source_unit='lots_assumed_1000',unit_multiplier=Decimal(1000),balance_shares=Decimal(values['balance'])*1000 if values.get('balance') is not None else None,
            identity_valid=valid,**values))
    return outprice,outmargin

def import_top(conn,mapping):
    universe=read(TOP/'universe.json');repair=read(TOP/'price_repair_report.json')['items']
    for i,item in enumerate(universe['items']):
        code=item['code'];sid=mapping[('top500',code)];market=item['market']
        # Price/margin inputs are the independently verified legacy projection.
        # Preserve linkage only when the matching archive spans the full projection.
        candidates=conn.execute(select(s.captures).where(s.captures.c.provider=='FinMind',s.captures.c.code==code).order_by(s.captures.c.id)).mappings().all()
        cap={}
        for dataset in (PRICE,MARGIN):
            matching=[r for r in candidates if r['dataset']==dataset and r['start_date']<=date(2021,10,1) and r['end_date']>=date(2026,11,3)]
            if not matching:raise ValueError(f'{code}: missing full-range lineage')
            cap[dataset]=matching[-1]
        rows=conn.execute(select(ProviderObservation.__table__).where(ProviderObservation.code==code).order_by(ProviderObservation.trading_date)).mappings().all()
        prices={};margins={}
        for row in rows:
            if any(row[k] is not None for k in ('open','high','low','close','volume','turnover')):
                prices[row['trading_date']]={k:row[k] for k in ('open','high','low','close','volume','turnover')}
            if any(row[k] is not None for k in ('margin_buy','margin_sell','repayment','previous_balance','balance','limit')):
                margins[row['trading_date']]={k:row[k] for k in ('margin_buy','margin_sell','repayment','previous_balance','balance','limit')}
        p,m=rows_for(sid,market,'FinMind',prices,margins,{k:v['id'] for k,v in cap.items()},
            {k:v['fetched_at'] or now() for k,v in cap.items()},'finmind_documented_daily')
        revision_rows(conn,s.prices,p);revision_rows(conn,s.margins,m)
        if not margins:issue(conn,f'no-margin:{sid}','no_margin_history',sid=sid,evidence={'code':code,'zero_imputation':False})
        for r in candidates:
            if r['dataset'] in EVENTS:
                payload=conn.scalar(select(s.blobs.c.payload).where(s.blobs.c.sha256==r['sha256']))
                event_records(conn,sid,r['id'],r['dataset'],payload)
        if code in repair:
            issue(conn,f'legacy-price-repair:{sid}','legacy_price_import_error',sid=sid,status='resolved',severity='info',evidence=repair[code])
        conn.commit()
        if (i+1)%25==0:print('top500 normalized',i+1,flush=True)

def import_delisted(conn,mapping):
    for i,item in enumerate(read(DELISTED/'universe.json')['items']):
        code=item['code'];sid=mapping[('delisted',code)];market=item['market'];caps={};parsed={}
        for dataset in (PRICE,MARGIN,*EVENTS):
            path=DELISTED/'raw'/code/(dataset+'.json');meta=read(path.with_name(dataset+'.meta.json'));raw=path.read_text(encoding='utf8')
            checksum=hashlib.sha256(raw.encode()).hexdigest()
            if checksum!=meta['sha256_utf8']:raise ValueError('delisted archive checksum mismatch')
            start=date.fromisoformat(meta['start']);end=date.fromisoformat(meta['end'])
            cid=record_capture(conn,raw,key=f'delisted:{code}:{dataset}:{checksum}',provider='FinMind',dataset=dataset,
                code=code,market=market,url=meta.get('source_url') or request_url(dataset,code,start,end),
                start=start,end=end,fetched_at=dt(meta.get('fetched_at')),http_status=meta['http_status'])
            caps[dataset]=(cid,dt(meta.get('fetched_at')) or now())
            if dataset in (PRICE,MARGIN):
                try:parsed[dataset]=parse_response(raw,meta['http_status'],dataset,code,start,end)
                except ProviderError as exc:
                    parsed[dataset]={};issue(conn,f'delisted-parse:{sid}:{dataset}','delisted_source_requires_review',sid=sid,evidence={'dataset':dataset,'error':str(exc),'capture_id':cid})
                if not json.loads(raw)['data']:issue(conn,f'delisted-empty:{sid}:{dataset}','empty_source_unverified',sid=sid,evidence={'dataset':dataset,'capture_id':cid})
            else:event_records(conn,sid,cid,dataset,raw)
        p,m=rows_for(sid,market,'FinMind',parsed[PRICE],parsed[MARGIN],{k:v[0] for k,v in caps.items()},
            {k:v[1] for k,v in caps.items()},'finmind_documented_daily')
        revision_rows(conn,s.prices,p);revision_rows(conn,s.margins,m);conn.commit()
        if (i+1)%10==0:print('delisted normalized',i+1,flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',choices=['archives','top','delisted','all'],default='all');a=p.parse_args()
    with engine.connect() as conn:
        mapping=import_universes(conn);conn.commit()
        if a.stage in ('archives','all'):legacy_captures(conn);conn.commit();print('archives imported',flush=True)
        if a.stage in ('top','all'):import_top(conn,mapping)
        if a.stage in ('delisted','all'):import_delisted(conn,mapping)
if __name__=='__main__':main()
