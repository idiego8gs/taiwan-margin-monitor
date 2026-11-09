"""FinMind research adapter. It never overwrites the official TWSE observations."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy import select
from .db import Base, engine, Session, ProviderObservation, ProviderResponse

log = logging.getLogger(__name__)
BASE_URL = 'https://api.finmindtrade.com/api/v4/data'
PRICE = 'TaiwanStockPrice'
MARGIN = 'TaiwanStockMarginPurchaseShortSale'
EVENTS = ('TaiwanStockDividend', 'TaiwanStockDividendResult',
          'TaiwanStockCapitalReductionReferencePrice', 'TaiwanStockSplitPrice')

class ProviderError(ValueError):
    pass

def request_url(dataset: str, code: str, start: date, end: date) -> str:
    if dataset not in (PRICE, MARGIN, *EVENTS):
        raise ProviderError('unsupported dataset')
    if not re.fullmatch(r'[A-Za-z0-9]{2,20}', code):
        raise ProviderError('invalid stock code')
    if end < start:
        raise ProviderError('end date precedes start date')
    return BASE_URL + '?' + urlencode({'dataset':dataset, 'data_id':code,
                                      'start_date':start.isoformat(), 'end_date':end.isoformat()})

def fetch_raw(url: str, token: str | None = None) -> tuple[str, int, datetime]:
    headers = {'User-Agent':'TaiwanMarginMonitor/0.1 local-research'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    for attempt in range(3):
        if engine.dialect.name=='postgresql':
            from .jobs import reserve_provider
            reserve_provider('FinMind',15)
        try:
            with urlopen(Request(url, headers=headers), timeout=45) as response:
                return response.read().decode('utf-8-sig'), response.status, datetime.now(timezone.utc)
        except HTTPError as exc:
            # 4xx normally needs a change of credentials, permissions or request, not a retry.
            body = exc.read().decode('utf-8-sig', errors='replace')
            if exc.code==429 and engine.dialect.name=='postgresql':
                from .jobs import defer_provider
                try:wait=int(exc.headers.get('Retry-After','900'))
                except ValueError:wait=900
                defer_provider('FinMind',max(15,wait))
            if exc.code == 429 or exc.code < 500:
                return body, exc.code, datetime.now(timezone.utc)
            if attempt == 2:
                return body, exc.code, datetime.now(timezone.utc)
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)
    raise ProviderError('request failed')

def _number(value, field: str, *, integer: bool = False):
    if value is None or isinstance(value, bool):
        raise ProviderError(f'missing {field}')
    try:
        value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ProviderError(f'invalid {field}') from None
    if not value.is_finite() or value < 0 or (integer and value != value.to_integral_value()):
        raise ProviderError(f'invalid {field}')
    return int(value) if integer else value

def parse_response(raw: str, status: int, dataset: str, code: str, start: date, end: date):
    try:
        obj = json.loads(raw, parse_float=Decimal)
    except (ValueError, TypeError):
        raise ProviderError(f'{dataset}: invalid JSON (HTTP {status})') from None
    if status != 200 or not isinstance(obj, dict) or obj.get('status') != 200 or not isinstance(obj.get('data'), list):
        message = obj.get('msg', 'invalid response') if isinstance(obj, dict) else 'invalid response'
        raise ProviderError(f'{dataset}: HTTP {status}, {message}')
    rows = {}
    for data in obj['data']:
        if not isinstance(data, dict) or data.get('stock_id') != code:
            raise ProviderError(f'{dataset}: unexpected stock identifier')
        try:
            day = date.fromisoformat(data['date'])
        except (ValueError, KeyError, TypeError):
            raise ProviderError(f'{dataset}: invalid date') from None
        if day < start or day > end or day in rows:
            raise ProviderError(f'{dataset}: duplicate or out-of-range date {day}')
        if dataset == PRICE:
            row = {target: _number(data.get(source), source) for target, source in
                   [('open','open'),('high','max'),('low','min'),('close','close')]}
            row['volume'] = _number(data.get('Trading_Volume'), 'Trading_Volume', integer=True)
            row['turnover'] = _number(data.get('Trading_money'), 'Trading_money', integer=True)
            # FinMind explicitly uses 0 for no-quote days. Treat it as missing, not a price.
            for key in ('open','high','low','close'):
                if row[key] == 0:
                    row[key] = None
            if all(row[k] is not None for k in ('open','high','low','close')):
                if not (row['low'] <= min(row['open'],row['close']) <= max(row['open'],row['close']) <= row['high']):
                    raise ProviderError(f'{dataset}: inconsistent OHLC on {day}')
        elif dataset == MARGIN:
            fields = [('margin_buy','MarginPurchaseBuy'),('margin_sell','MarginPurchaseSell'),
                      ('repayment','MarginPurchaseCashRepayment'),('previous_balance','MarginPurchaseYesterdayBalance'),
                      ('balance','MarginPurchaseTodayBalance'),('limit','MarginPurchaseLimit')]
            row = {target:_number(data.get(source), source, integer=True) for target,source in fields}
        else:
            raise ProviderError('event payloads are archived but not normalized')
        rows[day] = row
    return rows

def _validate_envelope(raw: str, status: int, dataset: str):
    try:
        obj = json.loads(raw)
    except ValueError:
        raise ProviderError(f'{dataset}: invalid JSON (HTTP {status})') from None
    if status != 200 or not isinstance(obj,dict) or obj.get('status') != 200 or not isinstance(obj.get('data'),list):
        message = obj.get('msg', 'invalid response') if isinstance(obj,dict) else 'invalid response'
        raise ProviderError(f'{dataset}: HTTP {status}, {message}')

def import_responses(code: str, start: date, end: date, responses: dict, session_factory=Session):
    """Archive raw responses first, then validate and upsert a research-only series.

    Responses are dataset -> (raw text, HTTP status, capture mode, fetched-at timestamp).
    A rejected response remains archived; no normalized rows are changed on parse failure.
    """
    for dataset in responses:
        request_url(dataset, code, start, end)  # validate before writing anything
    with session_factory.begin() as db:
        for dataset, (raw, status, mode, fetched_at) in responses.items():
            db.add(ProviderResponse(provider='FinMind',dataset=dataset,code=code,
                  start_date=start,end_date=end,source_url=request_url(dataset,code,start,end),
                  http_status=status,payload=raw,sha256=hashlib.sha256(raw.encode('utf-8')).hexdigest(),
                  capture_mode=mode,fetched_at=fetched_at))
    if PRICE not in responses or MARGIN not in responses:
        raise ProviderError('both price and margin responses are required')
    prices = parse_response(responses[PRICE][0], responses[PRICE][1], PRICE, code, start, end)
    margins = parse_response(responses[MARGIN][0], responses[MARGIN][1], MARGIN, code, start, end)
    for dataset in responses:
        if dataset in EVENTS:
            _validate_envelope(responses[dataset][0],responses[dataset][1],dataset)
    with session_factory.begin() as db:
        existing = db.scalars(select(ProviderObservation).where(
            ProviderObservation.provider=='FinMind', ProviderObservation.code==code,
            ProviderObservation.trading_date>=start, ProviderObservation.trading_date<=end)).all()
        existing_by_date = {row.trading_date: row for row in existing}
        dates = sorted(set(prices) | set(margins) | set(existing_by_date))
        for day in dates:
            row = existing_by_date.get(day)
            if row is None:
                row = ProviderObservation(provider='FinMind',code=code,trading_date=day)
                db.add(row)
            # Missing datasets remain NULL, never fabricated or reused from earlier imports.
            for key in ('open','high','low','close','volume','turnover'):
                setattr(row,key,prices.get(day,{}).get(key))
            for key in ('margin_buy','margin_sell','repayment','previous_balance','balance','limit'):
                setattr(row,key,margins.get(day,{}).get(key))
    return {'code':code,'price_rows':len(prices),'margin_rows':len(margins),'dates':len(dates)}

def diagnostics(rows):
    """Check observed rows only; does not claim that the trading calendar is complete."""
    issues=[]; prev=None
    for row in rows:
        day=row.trading_date.isoformat()
        if row.close is None:
            issues.append({'date':day,'kind':'missing_price'})
        fields=(row.previous_balance,row.margin_buy,row.margin_sell,row.repayment,row.balance)
        if any(x is None for x in fields):
            issues.append({'date':day,'kind':'missing_margin'})
            prev=None
            continue
        old=row.previous_balance-row.margin_sell-row.repayment
        if old < 0 or old+row.margin_buy != row.balance:
            issues.append({'date':day,'kind':'balance_identity_mismatch'})
        if prev is not None and prev.balance != row.previous_balance:
            issues.append({'date':day,'kind':'previous_balance_conflict',
                           'previous_date':prev.trading_date.isoformat(),
                           'previous_day_balance':prev.balance,'reported_previous_balance':row.previous_balance})
        prev=row
    return issues

def main():
    p=argparse.ArgumentParser(description='Import FinMind per-stock history into research tables')
    p.add_argument('--stock',required=True); p.add_argument('--start',required=True); p.add_argument('--end',required=True)
    p.add_argument('--events',action='store_true',help='also archive corporate-action responses, without normalization')
    p.add_argument('--from-directory',type=Path,help='import DATASET.json files from this directory; fetch time is unknown')
    args=p.parse_args(); start=date.fromisoformat(args.start); end=date.fromisoformat(args.end)
    datasets=(PRICE,MARGIN,*EVENTS) if args.events else (PRICE,MARGIN)
    Base.metadata.create_all(engine)
    responses={}
    for dataset in datasets:
        url=request_url(dataset,args.stock,start,end)
        if args.from_directory:
            raw=(args.from_directory/(dataset+'.json')).read_text(encoding='utf-8-sig')
            responses[dataset]=(raw,200,'file',None)
        else:
            raw,status,fetched=fetch_raw(url,os.environ.get('FINMIND_TOKEN'))
            responses[dataset]=(raw,status,'live',fetched)
            time.sleep(.5)
    result=import_responses(args.stock,start,end,responses)
    with Session() as db:
        rows=db.scalars(select(ProviderObservation).where(ProviderObservation.provider=='FinMind',
                        ProviderObservation.code==args.stock).order_by(ProviderObservation.trading_date)).all()
        issues=diagnostics(rows)
    print(json.dumps({**result,'issues':issues,'scope':'research_only'},ensure_ascii=False,default=str))

if __name__=='__main__':
    main()
