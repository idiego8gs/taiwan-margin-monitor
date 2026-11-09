"""Import only verified current-market history from archived raw responses.

FinMind's emerging-market `open` is a prior-day average rather than an actual
open. The official current-market listing date is a conservative cutover; it
can discard valid older history from a different listed market after a transfer.
Raw input is never rewritten and no price is imputed.
"""
from datetime import date,datetime,timezone
from pathlib import Path
import argparse,hashlib,json,os
from sqlalchemy import select
from .db import Session,ProviderObservation
from .finmind import PRICE,MARGIN,parse_response,ProviderError

PRICE_FIELDS=('open','high','low','close','volume','turnover')
MARGIN_FIELDS=('margin_buy','margin_sell','repayment','previous_balance','balance','limit')

def prepare(raw, dataset, code, cutoff, start, end):
    obj=json.loads(raw)
    if obj.get('status')!=200 or not isinstance(obj.get('data'),list):
        raise ProviderError('invalid archived response')
    # Date values must be parsed before any filtering; a malformed row cannot be silently dropped.
    keep=[];discarded=0
    for record in obj['data']:
        if record.get('stock_id')!=code: raise ProviderError('unexpected stock identifier')
        try: day=date.fromisoformat(record['date'])
        except (KeyError,ValueError,TypeError): raise ProviderError('invalid archived date') from None
        if day<start or day>end: raise ProviderError('out-of-range archived date')
        if day<cutoff: discarded+=1
        else: keep.append(record)
    data={'status':200,'data':keep}
    parsed=parse_response(json.dumps(data),200,dataset,code,start,end)
    return parsed,discarded

def listings(twse,tpex,as_of):
    result={}
    for market,rows,codekey,datekey,reportkey in [('TWSE',twse,'公司代號','上市日期','出表日期'),
                                                   ('TPEx',tpex,'SecuritiesCompanyCode','DateOfListing','Date')]:
        for r in rows:
            code=r.get(codekey); value=str(r.get(datekey,'')); reported=str(r.get(reportkey,''))
            if not value.isdigit() or len(value)!=8 or not reported.isdigit() or len(reported)!=7:continue
            try:
                effective=date(int(value[:4]),int(value[4:6]),int(value[6:8]))
                reportday=date(int(reported[:3])+1911,int(reported[3:5]),int(reported[5:7]))
            except ValueError:continue
            if reportday>as_of: continue
            key=(market,code)
            if key in result: raise ValueError('duplicate listing record')
            result[key]=(effective,reportday)
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--universe-dir',default='research/top500_20261103');p.add_argument('--start',default='2021-10-01');p.add_argument('--end',default='2026-11-03');args=p.parse_args()
    root=Path(args.universe_dir);start=date.fromisoformat(args.start);end=date.fromisoformat(args.end)
    universe=json.loads((root/'universe.json').read_text(encoding='utf8'))
    progress=json.loads((root/'backfill/progress.json').read_text(encoding='utf8'))
    markets={r['code']:r['market'] for r in universe['items']}
    dates=listings(json.loads((root/'mops.json').read_text(encoding='utf8')),json.loads((root/'tpex_basic_20261104.json').read_text(encoding='utf8')),end)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'as_of':str(end),'start':str(start),
            'method':'保守排除目前市場掛牌日前資料；興櫃前日均價不可當開盤價，且可能排除舊市場有效行情',
            'original_response_retained':True,'items':{},'errors':{}}
    for code,error in progress['errors'].items():
        try:
            listing,report_date=dates[(markets[code],code)]
            if listing>end: raise ProviderError('listing date after study end')
            cutoff=max(start,listing);rawdir=root/'backfill/raw'/code;datasets={}
            for dataset in (PRICE,MARGIN):
                path=rawdir/(dataset+'.json');raw=path.read_text(encoding='utf8')
                parsed,discard=prepare(raw,dataset,code,cutoff,start,end)
                datasets[dataset]=(parsed,discard,hashlib.sha256(raw.encode('utf8')).hexdigest())
            prices,discard_price,sha_price=datasets[PRICE];margins,discard_margin,sha_margin=datasets[MARGIN]
            if not prices:raise ProviderError('no prices after cutoff')
            import_status='imported'
            with Session.begin() as db:
                existing=db.scalars(select(ProviderObservation).where(ProviderObservation.provider=='FinMind',
                    ProviderObservation.code==code,ProviderObservation.trading_date>=cutoff,ProviderObservation.trading_date<=end)).all()
                if existing:
                    by_day={row.trading_date:row for row in existing}
                    if set(by_day)!=(set(prices)|set(margins)):
                        raise ProviderError('existing dates differ; no overwrite')
                    for day,row in by_day.items():
                        if any(getattr(row,field)!=prices.get(day,{}).get(field) for field in PRICE_FIELDS) or any(
                           getattr(row,field)!=margins.get(day,{}).get(field) for field in MARGIN_FIELDS):
                            raise ProviderError('existing values differ; no overwrite')
                    import_status='already_imported'
                else:
                    for day in sorted(set(prices)|set(margins)):
                        row=ProviderObservation(provider='FinMind',code=code,trading_date=day)
                        for field in PRICE_FIELDS:setattr(row,field,prices.get(day,{}).get(field))
                        for field in MARGIN_FIELDS:setattr(row,field,margins.get(day,{}).get(field))
                        db.add(row)
            report['items'][code]={'market':markets[code],'current_market_listing_date':str(listing),
                 'official_snapshot_date':str(report_date),'cutoff':str(cutoff),'status':import_status,'price_rows':len(prices),'margin_rows':len(margins),
                 'discarded_pre_cutoff_price_rows':discard_price,'discarded_pre_cutoff_margin_rows':discard_margin,
                 'price_sha256':sha_price,'margin_sha256':sha_margin,'prior_error':error}
            print(code,import_status,len(prices),len(margins),flush=True)
        except (KeyError,ValueError,ProviderError,OSError) as exc:
            report['errors'][code]=str(exc);print(code,'failed',str(exc),flush=True)
        # Never write the shared progress checkpoint: corporate-action import may run concurrently.
        target=root/'price_repair_report.json';tmp=target.with_suffix('.tmp')
        tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');os.replace(tmp,target)
    print('completed',len(report['items']),'failed',len(report['errors']),flush=True)

if __name__=='__main__':main()
