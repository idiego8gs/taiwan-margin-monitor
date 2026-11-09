"""Slow, restartable FinMind per-security historical ingestion."""
from datetime import date,datetime,timezone
from pathlib import Path
import argparse,json,os,time,hashlib
from .db import Base,engine,Session,ProviderResponse
from .finmind import PRICE,MARGIN,EVENTS,request_url,fetch_raw,import_responses,ProviderError,_validate_envelope
from sqlalchemy import select
from urllib.error import URLError

def atomic_json(path,obj):
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str),encoding='utf8')
    os.replace(tmp,path)

def find_archived_event(session_factory, code, dataset, start, end):
    """Reuse only a valid archived response covering the entire requested range."""
    with session_factory() as db:
        rows=db.scalars(select(ProviderResponse).where(ProviderResponse.provider=='FinMind',
            ProviderResponse.dataset==dataset,ProviderResponse.code==code,
            ProviderResponse.start_date<=start,ProviderResponse.end_date>=end,
            ProviderResponse.http_status==200).order_by(ProviderResponse.id.desc())).all()
        for row in rows:
            try:
                _validate_envelope(row.payload,row.http_status,dataset)
                data=json.loads(row.payload)['data']
                if any(not isinstance(item,dict) or item.get('stock_id')!=code for item in data):
                    continue
                return True
            except (ProviderError,ValueError,TypeError):
                continue
    return False

def acquire_work_lock(out):
    """Prevent price/event workers from racing over the same checkpoint.

    The OS releases the lock if a worker crashes; the marker file is harmless.
    """
    path=out/'.backfill.lock'
    handle=path.open('a+b')
    try:
        if os.name=='nt':
            import msvcrt
            if path.stat().st_size==0:
                handle.write(b'0');handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError(f'another backfill worker owns {path}') from exc
    return handle

def main():
    p=argparse.ArgumentParser();p.add_argument('--universe',required=True);p.add_argument('--output',required=True)
    p.add_argument('--start',default='2021-10-01');p.add_argument('--end',default='2026-11-03')
    p.add_argument('--interval',type=float,default=15);p.add_argument('--stage',choices=['prices','events'],default='prices');a=p.parse_args()
    if a.interval<12: p.error('minimum interval is 12 seconds (300 requests/hour)')
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    lock=acquire_work_lock(out)
    try:
        execute(a,out)
    finally:
        lock.close()

def execute(a,out):
    start=date.fromisoformat(a.start);end=date.fromisoformat(a.end)
    universe=json.loads(Path(a.universe).read_text(encoding='utf8'))['items']
    Base.metadata.create_all(engine)
    state_path=out/'progress.json'
    universe_hash=hashlib.sha256(Path(a.universe).read_bytes()).hexdigest()
    state=json.loads(state_path.read_text(encoding='utf8')) if state_path.exists() else {'completed':{},'errors':{},'started_at':datetime.now(timezone.utc).isoformat()}
    if state.get('universe_sha256',universe_hash)!=universe_hash or state.get('start',str(start))!=str(start) or state.get('end',str(end))!=str(end):
        raise ValueError('checkpoint dates or universe changed; use a new output directory')
    state['universe_sha256']=universe_hash
    state['target']=len(universe);state['start']=str(start);state['end']=str(end)
    state.setdefault('events_completed',{});state.setdefault('event_errors',{})
    atomic_json(state_path,state)
    for item in universe:
        code=item['code']
        completed=state['completed'] if a.stage=='prices' else state.setdefault('events_completed',{})
        if code in completed: continue
        responses={};code_dir=out/'raw'/code;code_dir.mkdir(parents=True,exist_ok=True)
        try:
            for dataset in ((PRICE,MARGIN) if a.stage=='prices' else EVENTS):
                if a.stage=='events' and find_archived_event(Session,code,dataset,start,end):
                    continue
                cache=code_dir/(dataset+'.json');metadata=code_dir/(dataset+'.meta.json')
                if cache.exists() and metadata.exists():
                    meta=json.loads(metadata.read_text(encoding='utf8'));raw=cache.read_text(encoding='utf8')
                    if meta.get('sha256') and meta['sha256']!=hashlib.sha256(raw.encode('utf8')).hexdigest():
                        raise ProviderError(f'{dataset}: cached response checksum mismatch')
                    responses[dataset]=(raw,meta['status'],'live',datetime.fromisoformat(meta['fetched_at']))
                else:
                    for attempt in range(5):
                        try:
                            raw,status,fetched=fetch_raw(request_url(dataset,code,start,end),os.getenv('FINMIND_TOKEN'))
                        except (URLError,TimeoutError,OSError) as exc:
                            if attempt==4: raise
                            print(json.dumps({'code':code,'dataset':dataset,'network_error':type(exc).__name__,'retry_in_seconds':60}),flush=True)
                            time.sleep(60)
                            continue
                        if status!=429: break
                        if attempt==4: break
                        print(json.dumps({'code':code,'dataset':dataset,'rate_limited':True,'retry_in_seconds':900}),flush=True)
                        time.sleep(900)
                    if status!=200:
                        (code_dir/(dataset+'.rejected.json')).write_text(raw,encoding='utf8')
                        raise ProviderError(f'{dataset} HTTP {status}; retry this stock on restart')
                    obj=json.loads(raw)
                    if obj.get('status')!=200 or not isinstance(obj.get('data'),list):
                        raise ProviderError(f'{dataset}: rejected response; retry on restart')
                    cache.write_text(raw,encoding='utf8');atomic_json(metadata,{'status':status,'fetched_at':fetched.isoformat(),
                      'sha256':hashlib.sha256(raw.encode('utf8')).hexdigest()})
                    responses[dataset]=(raw,status,'live',fetched)
                    time.sleep(a.interval)
            if a.stage=='prices':
                result=import_responses(code,start,end,responses)
                completed[code]={**result,'rank':item['rank'],'market':item['market']}
            else:
                for dataset,(raw,status,mode,fetched) in responses.items():
                    _validate_envelope(raw,status,dataset)
                    digest=hashlib.sha256(raw.encode('utf8')).hexdigest()
                    with Session.begin() as db:
                        found=db.scalar(select(ProviderResponse.id).where(ProviderResponse.provider=='FinMind',
                            ProviderResponse.dataset==dataset,ProviderResponse.code==code,ProviderResponse.start_date==start,
                            ProviderResponse.end_date==end,ProviderResponse.sha256==digest))
                        if found is None:
                            db.add(ProviderResponse(provider='FinMind',dataset=dataset,code=code,start_date=start,end_date=end,
                                   source_url=request_url(dataset,code,start,end),http_status=status,payload=raw,sha256=digest,
                                   capture_mode=mode,fetched_at=fetched))
                result={'code':code,'event_datasets':len(EVENTS),'new_responses':len(responses)}
                completed[code]=result
            errors=state['errors'] if a.stage=='prices' else state['event_errors']
            errors.pop(code,None)
            state['updated_at']=datetime.now(timezone.utc).isoformat();atomic_json(state_path,state)
            print(json.dumps({'stage':a.stage,'completed':len(completed),'target':len(universe),**result},ensure_ascii=False),flush=True)
        except Exception as exc:
            errors=state['errors'] if a.stage=='prices' else state['event_errors']
            errors[code]={'error':str(exc),'time':datetime.now(timezone.utc).isoformat()}
            atomic_json(state_path,state)
            print(json.dumps({'code':code,'error':str(exc)},ensure_ascii=False),flush=True)
            # Stop for provider failures, but let other securities proceed after an isolated schema or data error.
            if not isinstance(exc,ProviderError) or 'HTTP ' in str(exc) or 'rejected response' in str(exc): break
    state['prices_finished']=len(state['completed'])==len(universe)
    state['events_finished']=len(state.get('events_completed',{}))==len(universe)
    state['finished']=state['prices_finished'] and state['events_finished'];atomic_json(state_path,state)
    if a.stage=='prices' and state['prices_finished']:
        print('price/margin complete; begin corporate-action stage',flush=True)
    if len(completed)!=len(universe): raise SystemExit(1)

if __name__=='__main__':main()
