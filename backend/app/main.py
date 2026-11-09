from contextlib import asynccontextmanager
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from pathlib import Path
import json
from time import monotonic
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_, inspect
from .db import Base, engine, Session, Observation, Watch, RawResponse, ProviderObservation
from .finmind import diagnostics
from .calculation import calculate
from .basket import calculate_basket
from .backtest import run as run_backtest
from .engine import DataQualityError
from .site_api import router as site_router

@asynccontextmanager
async def lifespan(app):
    from .warehouse import require_schema
    require_schema(engine)
    yield
app=FastAPI(title='Taiwan Margin Monitor', version='0.1.0', lifespan=lifespan)
app.include_router(site_router)
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:3000','http://127.0.0.1:3000'],allow_methods=['GET','POST','DELETE'],allow_headers=['*'])
visits=defaultdict(deque)
@app.middleware('http')
async def rate_limit(request: Request, call_next):
    key=request.client.host if request.client else 'unknown'; now=monotonic(); bucket=visits[key]
    while bucket and bucket[0] < now-60: bucket.popleft()
    if len(bucket)>=120:
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'rate limit exceeded'},status_code=429)
    bucket.append(now)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    return response

def item(o):
    return {k:getattr(o,k) for k in ['code','name','market','trading_date','open','high','low','close','volume','turnover','margin_buy','margin_sell','repayment','previous_balance','balance','limit','quality']}

@app.get('/api/data-status')
def status():
    with Session() as db:
        latest=db.scalar(select(func.max(Observation.trading_date)))
        count=db.scalar(select(func.count()).select_from(Observation))
        days=db.scalar(select(func.count(func.distinct(Observation.trading_date))))
        anomalies=db.scalar(select(func.count()).select_from(Observation).where(Observation.quality=='balance_mismatch'))
        research_count=db.scalar(select(func.count()).select_from(ProviderObservation))
        from . import warehouse_schema as schema
        if inspect(db.get_bind()).has_table('data_checks') and db.scalar(select(func.count()).select_from(schema.universes)):
            from .data_status import status as warehouse_status
            return warehouse_status(db.connection())|{'records':count,'days':days,'anomalies':anomalies,'research_records':research_count}
        return {'latest_data_date':latest,'records':count,'days':days,'anomalies':anomalies,
                'research_records':research_count,'published_snapshot':None,'status':'incomplete',
                'calibration':'unavailable','sources':['TWSE historical reports','TPEx historical reports','FinMind research'],
                'limitations':['上市櫃完整歷史、官方市場融資金額校準與公司行動尚未完成',
                               'TPEx 官方歷史行情為整股口徑；FinMind 的成交量口徑可能不同',
                               '可查看有明確品質標記的研究試算；完整校準快照尚未發布']}

@app.get('/api/stocks')
def stocks(q:str='',market:str='',page:int=1,limit:int=50):
    page=max(1,page); limit=max(1,min(limit,100))
    with Session() as db:
        latest=db.scalar(select(func.max(Observation.trading_date)))
        query=select(Observation).where(Observation.trading_date==latest)
        if q: query=query.where(or_(Observation.code.contains(q),Observation.name.contains(q)))
        if market: query=query.where(Observation.market==market)
        rows=db.scalars(query.order_by(Observation.code).offset((page-1)*limit).limit(limit)).all()
        return {'items':[item(x) for x in rows],'page':page,'latest_date':latest}

@app.get('/api/rankings')
def rankings():
    return {'items':[],'reason':'估算尚未通過資料完整性檢查，排行榜未發布'}

@app.get('/api/research/stocks/{code}/history')
def research_history(code:str):
    """Third-party observations are kept separate from official market snapshots."""
    with Session() as db:
        rows=db.scalars(select(ProviderObservation).where(
            ProviderObservation.provider=='FinMind',ProviderObservation.code==code
        ).order_by(ProviderObservation.trading_date)).all()
        fields=['code','trading_date','open','high','low','close','volume','turnover',
                'margin_buy','margin_sell','repayment','previous_balance','balance','limit']
        return {'provider':'FinMind','scope':'research_only',
                'items':[{key:getattr(row,key) for key in fields} for row in rows],
                'issues':diagnostics(rows),
                'limitations':['交易日曆覆蓋與公司行動尚未完整核對','歷史行情可能有事後修訂，非 point-in-time 資料','僅提供未校準研究試算']}

@app.get('/api/research/stocks/{code}/comparison')
def research_comparison(code:str):
    with Session() as db:
        official_rows=db.scalars(select(Observation).where(Observation.code==code)
                                 .order_by(Observation.trading_date.desc())).all()
        for official in official_rows:
            research=db.scalar(select(ProviderObservation).where(
                ProviderObservation.provider=='FinMind',ProviderObservation.code==code,
                ProviderObservation.trading_date==official.trading_date))
            if research:
                fields=['open','high','low','close','volume','turnover','margin_buy','margin_sell',
                        'repayment','previous_balance','balance','limit']
                return {'date':official.trading_date,'market':official.market,
                        'official':{k:getattr(official,k) for k in fields},
                        'finmind':{k:getattr(research,k) for k in fields},
                        'note':'來源口徑可能不同；TPEx 網站歷史行情為整股資料，不能直接判定成交量差異是錯誤。'}
        return {'date':None,'official':None,'finmind':None,'note':'目前沒有同一日期的兩來源資料'}

@app.get('/api/stocks/{code}/history')
def history(code:str):
    with Session() as db:
        rows=db.scalars(select(Observation).where(Observation.code==code).order_by(Observation.trading_date)).all()
        return {'items':[item(x) for x in rows],'quality':'公司行動尚未核實，研究試算另見 estimate API'}

def decimal_strings(value):
    if isinstance(value,Decimal): return format(value,'f')
    if isinstance(value,list): return [decimal_strings(v) for v in value]
    if isinstance(value,dict): return {k:decimal_strings(v) for k,v in value.items()}
    return value

@app.get('/api/research/market-basket')
def market_basket(codes:str, start_at:date|None=None, as_of:date|None=None,
                  finance_ratio:Decimal=Query(default=Decimal('0.6'),gt=0,le=1),
                  buy_price_method:Literal['vwap','close']='vwap'):
    """Market-wide margin balances for selected stocks, not a person's account."""
    symbols=[code.strip().upper() for code in codes.split(',') if code.strip()]
    with Session() as db:
        try:
            result=calculate_basket(db,symbols,start_at=start_at,as_of=as_of,finance_ratio=finance_ratio,
                                    buy_price_method=buy_price_method)
        except DataQualityError as exc:
            raise HTTPException(422,str(exc)) from exc
    return decimal_strings(result)

@app.get('/api/research/backtest')
def research_backtest(codes:str='3363,2308,8039,2454', start_at:date=date(2026,7,8),
                      as_of:date=date(2026,11,3)):
    symbols=[x.strip().upper() for x in codes.split(',') if x.strip()]
    import re
    if not 1<=len(symbols)<=10 or len(set(symbols))!=len(symbols) or any(not re.fullmatch(r'[A-Za-z0-9]{2,20}',x) for x in symbols):
        raise HTTPException(422,'請輸入 1 至 10 個不重複的證券代號')
    if start_at>=as_of: raise HTTPException(422,'起算日期須早於結束日期')
    with Session() as db:
        try: result=run_backtest(db,symbols,start_at,as_of)
        except DataQualityError as exc: raise HTTPException(422,str(exc)) from exc
    return decimal_strings(result)

@app.get('/api/research/top500/status')
def top500_status():
    with Session() as db:
        from . import warehouse_schema as schema
        if inspect(db.get_bind()).has_table('data_checks') and db.scalar(select(func.count()).select_from(schema.universes)):
            from .data_status import status as warehouse_status
            return warehouse_status(db.connection())
    root=Path(__file__).resolve().parents[2]/'research'/'top500_20261103'
    universe=root/'universe.json';progress=root/'backfill'/'progress.json'
    if not universe.exists(): return {'available':False,'reason':'尚未建立 500 檔名單'}
    selected=json.loads(universe.read_text(encoding='utf8'))
    if not progress.exists(): return {'available':True,'selected':len(selected['items']),'backfill_started':False}
    data=json.loads(progress.read_text(encoding='utf8'))
    repair_path=root/'price_repair_report.json'
    repair=json.loads(repair_path.read_text(encoding='utf8')).get('items',{}) if repair_path.exists() else {}
    repaired={code:entry for code,entry in repair.items() if entry.get('status') in ('imported','already_imported')}
    unresolved={code:entry for code,entry in data.get('errors',{}).items() if code not in repaired}
    return {'available':True,'as_of':selected['as_of'],'selected':len(selected['items']),
            'price_margin_completed':len(data.get('completed',{})),
            'price_repaired':len(repaired),'repaired_missing_margin':sum(x.get('margin_rows',0)==0 for x in repaired.values()),
            'events_completed':len(data.get('events_completed',{})),'errors':unresolved,'event_errors':data.get('event_errors',{}),
            'prices_finished':data.get('prices_finished',False),'events_finished':data.get('events_finished',False),
            'last_updated':data.get('updated_at'),'note':'資料抓取完成不代表估算或回測已通過品質檢查；此狀態不代表程序仍在執行。'}

@app.get('/api/data-quality')
def data_quality(code:str|None=None,kind:str|None=None,status:str|None=None,
                 page:int=Query(1,ge=1),limit:int=Query(50,ge=1,le=200)):
    from .data_status import quality
    with Session() as db:return quality(db.connection(),code,kind,status,page,limit)

@app.get('/api/jobs')
def jobs(status:str|None=None,page:int=Query(1,ge=1),limit:int=Query(30,ge=1,le=100)):
    from . import warehouse_schema as s
    with Session() as db:
        query=select(s.jobs)
        if status:query=query.where(s.jobs.c.status==status)
        total=db.scalar(select(func.count()).select_from(query.subquery()))
        rows=db.execute(query.order_by(s.jobs.c.updated_at.desc()).offset((page-1)*limit).limit(limit)).mappings().all()
        return {'items':[dict(r) for r in rows],'total':total,'page':page}

@app.get('/api/jobs/{job_id}')
def job_detail(job_id:str):
    from . import warehouse_schema as s
    with Session() as db:
        row=db.execute(select(s.jobs).where(s.jobs.c.id==job_id)).mappings().first()
        if not row:raise HTTPException(404,'找不到工作')
        return dict(row)

@app.get('/api/research/runs')
def research_runs(limit:int=Query(20,ge=1,le=100)):
    from . import warehouse_schema as s
    with Session() as db:
        rows=db.execute(select(s.runs.c.id,s.runs.c.data_version,s.runs.c.status,s.runs.c.created_at,
            s.executions.c.heartbeat_at,s.executions.c.progress_json,s.executions.c.last_error)
            .join(s.executions,s.executions.c.run_id==s.runs.c.id).order_by(s.runs.c.created_at.desc()).limit(limit)).mappings().all()
        return {'items':[{**dict(r),'progress':json.loads(r['progress_json'])} for r in rows]}

@app.get('/api/research/runs/{run_id}')
def research_run_detail(run_id:str):
    from . import warehouse_schema as s
    with Session() as db:
        row=db.execute(select(s.runs,s.executions.c.heartbeat_at,s.executions.c.progress_json,s.executions.c.last_error)
            .join(s.executions,s.executions.c.run_id==s.runs.c.id).where(s.runs.c.id==run_id)).mappings().first()
        if not row:raise HTTPException(404,'找不到研究工作')
        return {**dict(row),'protocol':json.loads(row['protocol_json']),'progress':json.loads(row['progress_json'])}

@app.get('/api/research/runs/{run_id}/results')
def research_run_results(run_id:str,quality:str|None=None,threshold:int|None=None,trend:str|None=None,
                         page:int=Query(1,ge=1),limit:int=Query(100,ge=1,le=800)):
    from . import warehouse_schema as s
    with Session() as db:
        state=db.scalar(select(s.runs.c.status).where(s.runs.c.id==run_id))
        if not state:raise HTTPException(404,'找不到研究工作')
        if state=='invalidated':raise HTTPException(409,'此研究版本因資料品質問題已失效，請使用重新計算後的版本。')
        rows=db.execute(select(s.results.c.metrics_json).where(s.results.c.run_id==run_id).order_by(s.results.c.result_key)).scalars()
        results=[json.loads(x) for x in rows]
        results=[r for r in results if (not quality or r.get('quality')==quality) and
            (threshold is None or r.get('threshold')==threshold) and (not trend or r.get('trend')==trend)]
        return {'items':results[(page-1)*limit:page*limit],'total':len(results),'page':page}

@app.get('/api/research/snapshots/{snapshot_id}/eligibility')
def research_eligibility(snapshot_id:str,code:str|None=None,page:int=Query(1,ge=1),limit:int=Query(100,ge=1,le=500)):
    from . import warehouse_schema as s
    with Session() as db:
        if not db.scalar(select(s.research_snapshots.c.id).where(s.research_snapshots.c.id==snapshot_id)):
            raise HTTPException(404,'找不到快照')
        q=select(s.eligibility,s.securities.c.code,s.securities.c.name).join(s.securities,s.securities.c.id==s.eligibility.c.security_id).where(s.eligibility.c.snapshot_id==snapshot_id)
        if code:q=q.where(s.securities.c.code==code)
        total=db.scalar(select(func.count()).select_from(q.subquery()))
        rows=db.execute(q.order_by(s.securities.c.code,s.eligibility.c.trading_date).offset((page-1)*limit).limit(limit)).mappings().all()
        return {'items':[{**dict(r),'reasons':json.loads(r['reasons_json'])} for r in rows],'total':total,'page':page}

@app.get('/api/research/warehouse/stocks/{code}/history')
def warehouse_history(code:str,start:date|None=None,end:date|None=None,
                      data_version:str|None=None,model_version:str|None=None,limit:int=Query(1500,ge=1,le=5000)):
    from . import warehouse_schema as s
    with Session() as db:
        q=select(s.snapshots).order_by(s.snapshots.c.created_at.desc())
        if data_version:q=q.where(s.snapshots.c.data_version==data_version)
        candidates=db.execute(q).mappings().all()
        selected=next((x for x in candidates if model_version is None or json.loads(x['summary_json']).get('model_version')==model_version),None)
        if not selected:return {'items':[],'data_version':data_version,'model_version':model_version}
        data_version=selected['data_version'];model_version=json.loads(selected['summary_json']).get('model_version')
        query=select(s.estimates,s.securities.c.code).join(s.securities,s.securities.c.id==s.estimates.c.security_id).where(
            s.securities.c.code==code,s.estimates.c.data_version==data_version,s.estimates.c.model_version==model_version)
        if start:query=query.where(s.estimates.c.trading_date>=start)
        if end:query=query.where(s.estimates.c.trading_date<=end)
        rows=db.execute(query.order_by(s.estimates.c.trading_date.desc()).limit(limit)).mappings().all()
        return decimal_strings({'items':[dict(r)|{'metrics':json.loads(r['metrics_json']),'quality':json.loads(r['quality_json'])} for r in reversed(rows)],
            'data_version':data_version,'model_version':model_version,'scope':'research_only',
            'quality_flags':['legacy_model_missing_par_value_events'] if model_version=='warehouse-close-0.6-v1' else []})

@app.get('/api/research/top500/estimates')
def top500_estimates():
    snapshot=Path(__file__).resolve().parents[2]/'research'/'top500_20261103'/'estimates.json'
    if not snapshot.exists(): raise HTTPException(404,'尚未產生研究試算快照')
    return json.loads(snapshot.read_text(encoding='utf8'))

@app.get('/api/stocks/{code}/estimate')
def stock_estimate(code:str, as_of:date|None=None, start_at:date|None=None,
                   finance_ratio:Decimal=Query(default=Decimal('0.6'),gt=0,le=1),
                   reconcile_decreases:bool=False, buy_price_method:Literal['vwap','close']='vwap'):
    """Uncalibrated research only. Strict mode is the default."""
    with Session() as db:
        try:
            result=calculate(db,code,as_of=as_of,start_at=start_at,finance_ratio=finance_ratio,
                             reconcile_decreases=reconcile_decreases,buy_price_method=buy_price_method)
        except DataQualityError as exc:
            raise HTTPException(422,str(exc)) from exc
    if result is None: raise HTTPException(404,'找不到可計算的股票資料')
    return decimal_strings(result)

@app.get('/api/stocks/{code}')
def stock(code:str):
    with Session() as db:
        row=db.scalar(select(Observation).where(Observation.code==code).order_by(Observation.trading_date.desc()).limit(1))
        if not row: raise HTTPException(404,'找不到股票或尚未回補')
        return item(row)

class WatchInput(BaseModel):
    code:str=Field(pattern=r'^[A-Za-z0-9]{2,20}$')
    maintenance_threshold:float=Field(default=140,gt=0,le=1000)
    distance_threshold:float=Field(default=5,ge=0,le=100)
@app.get('/api/watchlist')
def watchlist():
    with Session() as db: return [{'code':w.code,'maintenance_threshold':w.maintenance_threshold,'distance_threshold':w.distance_threshold,'alert':None} for w in db.scalars(select(Watch)).all()]
@app.post('/api/watchlist')
def add_watch(data:WatchInput):
    with Session.begin() as db:
        w=db.get(Watch,data.code)
        if not w: w=Watch(code=data.code); db.add(w)
        w.maintenance_threshold=data.maintenance_threshold; w.distance_threshold=data.distance_threshold
    return {'saved':True,'alerts':'完整校準快照未發布，警示暫不觸發'}
@app.delete('/api/watchlist/{code}')
def delete_watch(code:str):
    with Session.begin() as db:
        w=db.get(Watch,code)
        if w: db.delete(w)
    return {'deleted':bool(w)}
