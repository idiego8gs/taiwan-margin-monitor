"""Fetch historical official evidence; never erase provider differences or breaks."""
from datetime import date,datetime,timezone
from decimal import Decimal
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode
import json
from sqlalchemy import select
from .db import engine
from . import warehouse_schema as s
from .warehouse import record_capture,issue,encode,insert_ignore,revision_rows,now
from .warehouse_ingest import rows_for
from .jobs import reserve_provider,enqueue
from .finmind import PRICE,MARGIN
from .market_totals import parse_total

class NotPublished(RuntimeError):pass
class SourceFailure(RuntimeError):
    def __init__(self,message,retry_after=60):super().__init__(message);self.retry_after=retry_after
def number(v,integer=False):
    if v is None or str(v).strip() in ('','--','---','----'):return None
    try:n=Decimal(str(v).replace(',','').strip())
    except Exception:raise ValueError('invalid source numeric field') from None
    if not n.is_finite() or n<0 or (integer and n!=int(n)):raise ValueError('invalid source numeric domain')
    return int(n) if integer else n
def fetch(provider,dataset,url,day,target_engine=engine,force=False):
    key=f'official:{provider}:{dataset}:{day}'
    if not force:
        with target_engine.connect() as c:
            r=c.execute(select(s.captures,s.blobs.c.payload).join(s.blobs,s.blobs.c.sha256==s.captures.c.sha256)
                .where(s.captures.c.provider==provider,s.captures.c.dataset==dataset,
                    s.captures.c.start_date==day,s.captures.c.end_date==day,s.captures.c.http_status==200)
                .order_by(s.captures.c.id.desc()).limit(1)).mappings().first()
            if r:
                obj=json.loads(r['payload'])
                if obj.get('stat') in ('OK','ok') and obj.get('tables'):return r['id'],r['payload'],r['fetched_at'] or r['imported_at']
    reserve_provider(provider,3,target_engine)
    stamp=now();status=200;retry=60
    try:
        with urlopen(Request(url,headers={'User-Agent':'TaiwanMarginMonitor/0.2 local-research'}),timeout=40) as response:
            raw=response.read().decode('utf-8-sig')
    except HTTPError as exc:
        status=exc.code;raw=exc.read().decode('utf-8-sig',errors='replace')
        try:retry=int(exc.headers.get('Retry-After',900 if status==429 else 60))
        except ValueError:retry=900 if status==429 else 60
    with target_engine.begin() as c:
        cid=record_capture(c,raw,key=key+':'+stamp.isoformat(),provider=provider,dataset=dataset,
            market=provider,start=day,end=day,url=url,fetched_at=stamp,http_status=status)
    if status!=200:
        if status==429:
            from .jobs import defer_provider
            defer_provider(provider,retry,target_engine)
        raise SourceFailure(f'{provider} HTTP {status}; capture {cid}',retry)
    return cid,raw,stamp

def urls(market,day,quotes=False):
    if market=='TWSE':
        if quotes:return 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?'+urlencode({'date':day.strftime('%Y%m%d'),'type':'ALLBUT0999','response':'json'})
        return 'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?'+urlencode({'date':day.strftime('%Y%m%d'),'selectType':'ALL','response':'json'})
    if quotes:return 'https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?'+urlencode({'date':day.strftime('%Y/%m/%d'),'type':'EW','response':'json'})
    return 'https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php?'+urlencode({'l':'zh-tw','d':f'{day.year-1911:03d}/{day.month:02d}/{day.day:02d}'})

def parse_margin(raw,market,day):
    obj=json.loads(raw)
    if obj.get('date')!=day.strftime('%Y%m%d') or str(obj.get('stat','')).lower()!='ok':raise NotPublished('official margin report absent or wrong date')
    candidates=[t for t in obj.get('tables',[]) if ('代號' in t.get('fields',[]) and '名稱' in t.get('fields',[]))]
    if len(candidates)!=1:raise ValueError('ambiguous official margin table')
    t=candidates[0];fields=[str(x).strip() for x in t['fields']]
    if market=='TWSE':
        expected=['代號','名稱','買進','賣出','現金償還','前日餘額','今日餘額','次一營業日限額']
        if fields[:8]!=expected:raise ValueError('TWSE margin column group changed')
        positions=[2,3,4,5,6,7]
    else:
        expected=['資買','資賣','現償','前資餘額(張)','資餘額','資限額']
        if any(fields.count(f)!=1 for f in expected):raise ValueError('TPEx margin column group changed')
        positions=[fields.index(f) for f in expected]
    rows={};names={}
    for r in t.get('data',[]):
        code=str(r[fields.index('代號')]).strip()
        if code in rows:raise ValueError('duplicate official margin code')
        values=[number(r[i],True) for i in positions]
        if any(v is None for v in values):raise ValueError('missing required official margin value')
        rows[code]=dict(zip(('margin_buy','margin_sell','repayment','previous_balance','balance','limit'),values));names[code]=r[fields.index('名稱')]
    if not rows:raise NotPublished('empty official margin report')
    return obj,rows,names

def parse_prices(raw,market,day):
    obj=json.loads(raw)
    required=['證券代號','開盤價','最高價','最低價','收盤價','成交股數','成交金額'] if market=='TWSE' else ['代號','開盤','最高','最低','收盤','成交股數','成交金額(元)']
    ts=[t for t in obj.get('tables',[]) if all(f in [str(x).strip() for x in t.get('fields',[])] for f in required)]
    if len(ts)!=1:raise NotPublished('official quote report absent or changed schema')
    t=ts[0]
    actual=obj.get('date') if market=='TWSE' else t.get('date')
    expected=day.strftime('%Y%m%d') if market=='TWSE' else f'{day.year-1911:03d}/{day.month:02d}/{day.day:02d}'
    if actual!=expected:raise ValueError('quote date mismatch')
    clean=[str(x).strip() for x in t['fields']]
    if any(clean.count(f)!=1 for f in required):raise ValueError('ambiguous quote columns')
    positions=[clean.index(f) for f in required];rows={}
    for r in t.get('data',[]):
        code=str(r[positions[0]]).strip()
        if code in rows:raise ValueError('duplicate official price')
        row={k:number(r[pos],k in ('volume','turnover')) for k,pos in zip(('open','high','low','close','volume','turnover'),positions[1:])}
        for k in ('open','high','low','close'):
            if row[k]==0:row[k]=None
        vals=[row[k] for k in ('open','high','low','close')]
        if all(v is not None for v in vals) and not row['low']<=min(row['open'],row['close'])<=max(row['open'],row['close'])<=row['high']:raise ValueError('official OHLC inconsistent')
        rows[code]=row
    return rows

def repair_day(market,day,quotes=False,force=False,target_engine=engine):
    cid,raw,stamp=fetch(market,'official_margin',urls(market,day),day,target_engine,force)
    _,margins,names=parse_margin(raw,market,day);prices={};pcid=cid;pstamp=stamp
    if quotes:
        pcid,praw,pstamp=fetch(market,'official_prices',urls(market,day,True),day,target_engine,force)
        prices=parse_prices(praw,market,day)
    count=0
    with target_engine.begin() as c:
        targets=c.execute(select(s.securities,s.listings.c.market,s.listings.c.valid_to).join(s.listings,s.listings.c.security_id==s.securities.c.id)
            .where(s.listings.c.market==market)).mappings().all()
        for sec in targets:
            code=sec['code'];sid=sec['id']
            if sec['identity_key'].startswith('current:') and day<date(2021,10,1):continue
            if sec['valid_to'] and day>sec['valid_to']:continue
            if code not in margins and code not in prices:continue
            p,m=rows_for(sid,market,market,{day:prices[code]} if code in prices else {},{day:margins[code]} if code in margins else {},
                {PRICE:pcid,MARGIN:cid},{PRICE:pstamp,MARGIN:stamp},'official_round_lot' if market=='TPEx' else 'official_MI_INDEX')
            count+=revision_rows(c,s.prices,p)+revision_rows(c,s.margins,m)
            research=c.execute(select(s.margins).where(s.margins.c.security_id==sid,s.margins.c.trading_date==day,s.margins.c.provider=='FinMind',s.margins.c.is_current==True)).mappings().first()
            if research and code in margins:
                delta={k:{'official':v,'finmind':research['margin_limit' if k=='limit' else k]} for k,v in margins[code].items() if v!=research['margin_limit' if k=='limit' else k]}
                issue(c,f'comparison:{sid}:{day}','official_provider_comparison',sid=sid,day=day,
                    status='needs_review' if delta else 'verified',severity='high' if delta else 'info',
                    evidence={'official_capture_id':cid,'provider_capture_id':research['capture_id'],'differences':delta,'source_name':names.get(code)})
        amount=parse_total(raw,market,day)
        if amount:insert_ignore(c,s.totals,dict(market=market,trading_date=day,capture_id=cid,amount_ntd=amount,
            scope_json=encode({'denominator_verified':False,'raw_unit':'thousand_ntd'})),['market','trading_date','capture_id'])
        insert_ignore(c,s.calendar,dict(market=market,trading_date=day,is_open=True,source_key=f'official-report:{cid}'),['market','trading_date','source_key'])
    return {'market':market,'date':str(day),'new_versions':count,'official_rows':len(margins),'quotes_requested':quotes}

def queue_repairs(target_engine=engine,limit=None):
    tasks={};priorities={}
    with target_engine.connect() as c:
        q=select(s.issues,s.listings.c.market,s.securities.c.identity_key).join(s.listings,s.issues.c.security_id==s.listings.c.security_id).join(s.securities,s.issues.c.security_id==s.securities.c.id).where(s.issues.c.status=='needs_review',s.issues.c.trading_date!=None,
            s.issues.c.kind.in_(['previous_balance_conflict','missing_price_or_suspension_unverified','missing_margin_or_eligibility_unverified']))
        dates={m:set(c.scalars(select(s.calendar.c.trading_date).where(s.calendar.c.market==m,s.calendar.c.is_open==True))) for m in ('TWSE','TPEx')}
        for row in c.execute(q).mappings():
            market=row['market'];day=row['trading_date'];key=(market,day)
            tasks[key]=tasks.get(key,False) or row['kind']=='missing_price_or_suspension_unverified'
            priority=30 if row['identity_key'].startswith('current:') else 80
            priorities[key]=min(priority,priorities.get(key,100))
            evidence=json.loads(row['evidence_json'])
            prior=evidence.get('previous_date')
            if prior:
                prior=date.fromisoformat(prior)
                if prior in dates[market]:
                    tasks.setdefault((market,prior),False);priorities[(market,prior)]=min(priority,priorities.get((market,prior),100))
    chosen=sorted(tasks.items(),key=lambda x:(x[0][1],x[0][0]))
    if limit is not None:chosen=chosen[:limit]
    for (market,day),quotes in chosen:
        enqueue('official_day',{'market':market,'date':str(day),'quotes':quotes},f'official-repair-v1:{market}:{day}:{quotes}',target_engine,priority=priorities.get((market,day),80))
    return {'queued_or_reused':len(chosen),'all_required_days':len(tasks)}
