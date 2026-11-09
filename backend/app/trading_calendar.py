"""Archive a third-party trading calendar used only to flag gaps in research series."""
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
import argparse, hashlib, json, os
from .db import Base, engine, Session, ProviderResponse
from .finmind import BASE_URL, fetch_raw, ProviderError

DATASET='TaiwanStockTradingDate'
def parse_calendar(raw, start, end):
    try:
        obj=json.loads(raw)
        if obj.get('status')!=200 or not isinstance(obj.get('data'),list): raise ValueError('bad status')
        dates=[date.fromisoformat(r['date']) for r in obj['data']]
    except (ValueError,KeyError,TypeError,AttributeError):
        raise ProviderError('invalid trading calendar') from None
    if len(set(dates))!=len(dates) or any(d<start or d>end for d in dates):
        raise ProviderError('invalid calendar range or duplicate dates')
    return sorted(dates)

def save(raw, start, end, *, mode='file', fetched_at=None, session_factory=Session):
    days=parse_calendar(raw,start,end)
    source_url=BASE_URL+'?'+urlencode({'dataset':DATASET,'start_date':start.isoformat(),'end_date':end.isoformat()})
    with session_factory.begin() as db:
        db.add(ProviderResponse(provider='FinMind',dataset=DATASET,code='MARKET',start_date=start,end_date=end,
            source_url=source_url,http_status=200,payload=raw,sha256=hashlib.sha256(raw.encode('utf-8')).hexdigest(),
            capture_mode=mode,fetched_at=fetched_at))
    return len(days)

def main():
    p=argparse.ArgumentParser();p.add_argument('--start',required=True);p.add_argument('--end',required=True)
    p.add_argument('--from-file',type=Path);args=p.parse_args()
    start=date.fromisoformat(args.start);end=date.fromisoformat(args.end)
    if end<start: p.error('invalid date range')
    if args.from_file:
        raw=args.from_file.read_text(encoding='utf-8-sig'); mode='file';fetched=None
    else:
        url=BASE_URL+'?'+urlencode({'dataset':DATASET,'start_date':start.isoformat(),'end_date':end.isoformat()})
        raw,status,fetched=fetch_raw(url,os.environ.get('FINMIND_TOKEN'))
        if status!=200: raise ProviderError(f'calendar HTTP {status}')
        mode='live'
    Base.metadata.create_all(engine)
    print(json.dumps({'dataset':DATASET,'dates':save(raw,start,end,mode=mode,fetched_at=fetched)}))
if __name__=='__main__':main()
