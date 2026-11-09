"""Restartable raw-only delisted data archive, gated by the active top-500 job.

Waits for the shared checkpoint lock and for successful completion of the
top-500 event phase; it does not alter normalized price/finance observations.
"""
from datetime import date,datetime,timezone
from pathlib import Path
from urllib.error import URLError
import argparse,hashlib,json,os,time
from .bulk_backfill import acquire_work_lock,atomic_json
from .finmind import PRICE,MARGIN,EVENTS,ProviderError,_validate_envelope,fetch_raw,parse_response,request_url

DATASETS=(PRICE,MARGIN,*EVENTS)

def timestamp():return datetime.now(timezone.utc).isoformat()

def valid_archive(raw,meta,dataset,code,start,end):
    if meta.get('dataset')!=dataset or meta.get('code')!=code or meta.get('start')!=str(start) or meta.get('end')!=str(end):
        return False
    if hashlib.sha256(raw.encode('utf8')).hexdigest()!=meta.get('sha256_utf8'):return False
    try:
        _validate_envelope(raw,meta['http_status'],dataset)
        rows=json.loads(raw)['data']
        return all(isinstance(r,dict) and r.get('stock_id')==code for r in rows)
    except (ProviderError,ValueError,KeyError,TypeError):return False

def quality(raw,dataset,code,start,end):
    """Validate documented normalized formats, but keep all source bytes regardless."""
    rows=json.loads(raw)['data'];report={'source_rows':len(rows),'quality':'archived_not_verified'}
    if dataset in (PRICE,MARGIN):
        try:report['parsed_rows']=len(parse_response(raw,200,dataset,code,start,end))
        except ProviderError as exc:report['quality']='requires_review';report['reason']=str(exc)
        if not rows:report['quality']='empty_unverified'
    return report

def set_state(path,state,**values):
    state.update(values);state['updated_at']=timestamp();atomic_json(path,state)

def wait_for_primary(path,state,primary,max_wait_seconds):
    """Return with shared lock held only after top-500 action stage finishes."""
    deadline=time.monotonic()+max_wait_seconds
    while time.monotonic()<deadline:
        try:gate=acquire_work_lock(primary)
        except RuntimeError:
            set_state(path,state,status='waiting_for_top500',reason='top500_worker_owns_lock')
        else:
            try:
                progress=json.loads((primary/'progress.json').read_text(encoding='utf8'))
                if progress.get('events_finished') is True:return gate
                set_state(path,state,status='waiting_for_top500',reason='top500_not_complete',
                          top500_events_completed=len(progress.get('events_completed',{})))
            except (OSError,ValueError,TypeError):
                set_state(path,state,status='waiting_for_top500',reason='top500_checkpoint_unreadable')
            gate.close()
        time.sleep(30)
    set_state(path,state,status='paused',reason='top500_did_not_complete_within_wait_limit')
    return None

def run():
    p=argparse.ArgumentParser();p.add_argument('--output',default='research/delisted_202110_202611');p.add_argument('--start',default='2015-01-01')
    p.add_argument('--primary-output',default='research/top500_20261103/backfill');p.add_argument('--interval',type=float,default=15);p.add_argument('--max-wait-hours',type=float,default=24);a=p.parse_args()
    if a.interval<12:p.error('minimum interval 12 seconds')
    if a.max_wait_hours<=0:p.error('max wait must be positive')
    start=date.fromisoformat(a.start);out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    own=acquire_work_lock(out)
    try:
        universe_path=out/'universe.json';universe=json.loads(universe_path.read_text(encoding='utf8'));sha=hashlib.sha256(universe_path.read_bytes()).hexdigest()
        state_path=out/'progress.json';state=json.loads(state_path.read_text(encoding='utf8')) if state_path.exists() else {'completed':{},'started_at':timestamp()}
        if state.get('universe_sha256',sha)!=sha or state.get('start',str(start))!=str(start):raise ValueError('queue changed; use a different output directory')
        set_state(state_path,state,status='waiting_for_top500',universe_sha256=sha,start=str(start),target=len(universe['items']))
        gate=wait_for_primary(state_path,state,Path(a.primary_output),a.max_wait_hours*3600)
        if gate is None:return
        try:
            set_state(state_path,state,status='running',reason=None)
            for item in universe['items']:
                code=item['code'];end=date.fromisoformat(item['end_date'])
                if end<start:raise ValueError('queue date precedes historical start')
                if code in state['completed']:continue
                dirpath=out/'raw'/code;dirpath.mkdir(parents=True,exist_ok=True);reports={}
                for dataset in DATASETS:
                    data_path=dirpath/(dataset+'.json');meta_path=dirpath/(dataset+'.meta.json')
                    if data_path.exists() and meta_path.exists():
                        raw=data_path.read_text(encoding='utf8');meta=json.loads(meta_path.read_text(encoding='utf8'))
                        if not valid_archive(raw,meta,dataset,code,start,end):raise ProviderError(dataset+': cached file or metadata mismatch')
                    else:
                        url=request_url(dataset,code,start,end)
                        for attempt in range(5):
                            try:raw,status,fetched=fetch_raw(url,os.getenv('FINMIND_TOKEN'))
                            except (URLError,TimeoutError,OSError) as exc:
                                if attempt==4:raise
                                set_state(state_path,state,status='retrying_network',code=code,dataset=dataset,attempt=attempt+1);time.sleep(60);continue
                            if status!=429:break
                            if attempt==4:break
                            set_state(state_path,state,status='rate_limited',code=code,dataset=dataset,attempt=attempt+1);time.sleep(900)
                        if status!=200:
                            (dirpath/(dataset+'.rejected.json')).write_text(raw,encoding='utf8')
                            raise ProviderError(dataset+': HTTP '+str(status))
                        meta={'dataset':dataset,'code':code,'start':str(start),'end':str(end),'source_url':url,
                              'fetched_at':fetched.isoformat(),'http_status':status,'sha256_utf8':hashlib.sha256(raw.encode('utf8')).hexdigest()}
                        if not valid_archive(raw,meta,dataset,code,start,end):
                            (dirpath/(dataset+'.rejected.json')).write_text(raw,encoding='utf8')
                            raise ProviderError(dataset+': invalid source data or stock ID')
                        tmp=data_path.with_suffix('.json.tmp');tmp.write_text(raw,encoding='utf8');os.replace(tmp,data_path);atomic_json(meta_path,meta)
                        set_state(state_path,state,status='running',code=code,dataset=dataset)
                        time.sleep(a.interval)
                    reports[dataset]=quality(raw,dataset,code,start,end)
                state['completed'][code]={'market_from_official_event':item['market'],'delisting_date':item['end_date'],'datasets':reports}
                set_state(state_path,state,status='running',code=code,dataset=None)
                print(json.dumps({'completed':len(state['completed']),'target':len(universe['items']),'code':code},ensure_ascii=False),flush=True)
            set_state(state_path,state,status='completed',code=None,dataset=None)
        except Exception as exc:
            set_state(state_path,state,status='paused',reason=str(exc));raise
        finally:gate.close()
    finally:own.close()

if __name__=='__main__':run()
