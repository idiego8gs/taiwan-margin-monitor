"""Replay all warehouse securities with one VWAP cost rule and export a daily line."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
import gzip
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path

from sqlalchemy import select, text

from . import warehouse_schema as db
from .db import engine

MODEL = 'wantgoo-vwap-0.6-v1'
FRACTION = .6
# TWSE TWTB7U confirms 6949's 20-for-1 ratio. Wantgoo's archived stock ratio
# supports applying it to old cost. The separately audited 4763 event retains
# nominal cost, as do other events whose cost treatment has not been verified.
COST_SPLITS = {('6949', date(2026, 9, 7)): 20.}
OUTPUT_FIELDS = ('security_id', 'code', 'market', 'date', 'segment', 'close', 'vwap', 'previous_lots',
                 'reported_previous_lots', 'lots', 'buys', 'cost', 'maintenance_pct', 'collateral_ntd',
                 'estimated_debt_ntd', 'seed_type', 'seed_weight', 'status', 'balance_revision',
                 'corporate_action', 'price_source', 'vwap_source', 'margin_source', 'price_revision', 'margin_revision')


def positive(value):
    return value is not None and isfinite(float(value)) and value > 0


def daily_cost(previous_cost, closing_lots, buys, vwap):
    retained = closing_lots - buys
    if not 0 <= retained <= closing_lots or (buys and not positive(vwap)):
        raise ValueError('Invalid financing inputs')
    if not retained:
        return vwap
    if not positive(previous_cost):
        raise ValueError('Missing cost of retained inventory')
    return (retained * previous_cost + buys * (vwap or 0)) / closing_lots


def load_candles(root):
    result = defaultdict(dict)
    if root is None:
        return result
    for path in sorted(root.glob('*-daily-candlesticks*.json')):
        if path.name.endswith('.meta.json'):
            continue
        raw = path.read_bytes()
        meta = json.loads(path.with_suffix('.meta.json').read_text(encoding='utf-8'))
        if meta['sha256'] != sha256(raw).hexdigest() or not meta['tls_verified']:
            raise ValueError(f'Invalid archive metadata: {path.name}')
        code = path.name.split('-')[0]
        for row in json.loads(raw):
            if positive(row['volume']) and positive(row['millionAmount']):
                day = (datetime.fromtimestamp(row['time'] / 1000, timezone.utc) + timedelta(hours=8)).date()
                result[code][day] = (float(row['close']), float(row['millionAmount']) * 1000 / row['volume'])
    return result


def valid_vwap(row):
    if not row or not positive(row['turnover_ntd']) or not positive(row['volume_shares']):
        return None
    vwap = float(row['turnover_ntd']) / row['volume_shares']
    if row['low'] is not None and vwap < float(row['low']) * .995:
        return None
    if row['high'] is not None and vwap > float(row['high']) * 1.005:
        return None
    return vwap


def archive_gap_rows(path):
    result = defaultdict(dict)
    if path is None:
        return result
    with path.open(encoding='utf-8-sig', newline='') as stream:
        for r in csv.DictReader(stream):
            day = date.fromisoformat(r['date'])
            fingerprint = sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()
            price = dict(close=float(r['close']) if r['close'] else None, low=None, high=None,
                         volume_shares=float(r['volume_shares']) if r['volume_shares'] else None,
                         turnover_ntd=float(r['amount_ntd']) if r['amount_ntd'] else None,
                         provider='TWSE archive', value_sha256=fingerprint)
            prev, buys, sells, repay, lots = (int(float(r[key])) for key in ('previous', 'buy', 'sell', 'repay', 'lots'))
            margin = dict(previous_balance=prev, margin_buy=buys, balance=lots, unit_multiplier=1000,
                          identity_valid=prev + buys - sells - repay == lots,
                          provider='TWSE archive', value_sha256=fingerprint)
            if day in result[r['code']]:
                raise ValueError('Duplicate official supplemental record')
            result[r['code']][day] = price, margin
    return result


def selected_observations(conn, table, security_id, market):
    field = 'close' if table is db.prices else 'balance'
    query = (select(table).where(table.c.security_id == security_id, table.c.is_current.is_(True),
                                  table.c.market == market, table.c[field].is_not(None))
             .order_by(table.c.trading_date, table.c.observed_at, table.c.id))
    selected = {}
    for row in conn.execute(query).mappings():
        day = row['trading_date']
        prior = selected.get(day)
        if table is db.prices:
            rank = (positive(row['close']), valid_vwap(row) is not None, row['provider'] == market)
        else:
            rank = (row['identity_valid'] is True, row['provider'] == market)
        if not prior or rank >= prior[0]:
            selected[day] = (rank, row)
    return {day: row for day, (_, row) in selected.items()}


def replay(security, market, prices, margins, sessions, actions, candles):
    code = security['code']
    previous_cost = previous_day = previous_lots = None
    segment = 0
    seed_type = None
    seed_weight = None
    for day in sorted(prices.keys() | margins.keys()):
        p, m = prices.get(day), margins.get(day)
        close = float(p['close']) if p and positive(p['close']) else None
        vwap = valid_vwap(p)
        vwap_source = p['provider'] if vwap is not None else None
        if day in candles and close == candles[day][0]:
            vwap, vwap_source = candles[day][1], 'Wantgoo'
        lots = m['balance'] if m else None
        buys = m['margin_buy'] if m else None
        reported = m['previous_balance'] if m else None
        cost = ratio = collateral = debt = None
        split_factor = COST_SPLITS.get((code, day), 1.)
        revision = previous_lots is not None and reported is not None and previous_lots != reported
        contiguous = previous_day == sessions.get(day)
        # TWSE announcement 1140010257 documents this interval without trades.
        if code == '4763' and str(previous_day) == '2025-06-18' and str(day) == '2025-06-30':
            contiguous = True
        if lots is not None and lots > 0 and close is not None:
            collateral = lots * float(m['unit_multiplier']) * close
        if not m:
            status = 'missing_margin'
        elif lots == 0:
            status = 'zero_inventory'
        elif m['identity_valid'] is not True or buys is None or not 0 <= buys <= lots:
            status = 'invalid_margin_flow'
        elif buys and vwap is None:
            status = 'missing_vwap'
        elif buys == lots:
            segment += 1
            cost, seed_type, seed_weight, status = daily_cost(None, lots, buys, vwap), 'new_inventory', 0., 'computed'
        elif previous_cost is not None and contiguous and reported and reported > 0:
            cost = daily_cost(previous_cost / split_factor, lots, buys, vwap)
            seed_weight *= (lots - buys) / lots
            status = 'computed'
        elif vwap is not None:
            # Old inventory's true cost is absent. Estimate once at a segment's
            # first traded VWAP, then propagate its weight through the same rule.
            segment += 1
            cost = daily_cost(vwap, lots, buys, vwap)
            seed_type = 'estimated_vwap_after_gap' if previous_day is not None else 'estimated_initial_vwap'
            seed_weight, status = (lots - buys) / lots, 'computed'
        else:
            status = 'missing_initial_cost'
        if cost is not None:
            debt = FRACTION * lots * float(m['unit_multiplier']) * cost
            if collateral is not None:
                ratio = 100 * collateral / debt
            else:
                status = 'missing_close'
        yield dict(security_id=security['id'], code=code, market=market, date=day, segment=segment if cost is not None else None,
                   close=close, vwap=vwap, previous_lots=previous_lots, reported_previous_lots=reported, lots=lots, buys=buys,
                   cost=cost, maintenance_pct=ratio, collateral_ntd=collateral, estimated_debt_ntd=debt,
                   seed_type=seed_type if cost is not None else None, seed_weight=seed_weight if cost is not None else None,
                   status=status, balance_revision=revision, corporate_action=(f'cost_split:{split_factor:g}' if split_factor != 1 else actions.get(day)),
                   price_source=p['provider'] if p else None,
                   vwap_source=vwap_source, margin_source=m['provider'] if m else None,
                   price_revision=p['value_sha256'] if p else None, margin_revision=m['value_sha256'] if m else None)
        previous_day, previous_cost, previous_lots = day, cost, lots


def collect_market(row, totals):
    if row['market'] != 'TWSE' or row['code'].startswith('00'):
        return
    out = totals[row['date']]
    if row['lots'] is None:
        out['missingMargin'] += 1
    elif row['lots'] > 0:
        out['positive'] += 1
        out['pricedCollateral'] += row['collateral_ntd'] or 0
        if row['maintenance_pct'] is not None:
            out['included'] += 1
            out['collateral'] += row['collateral_ntd']
            out['debt'] += row['estimated_debt_ntd']
            out['seedDebt'] += row['estimated_debt_ntd'] * row['seed_weight']
        else:
            out['excluded'] += 1


def market_rows(totals, calendar, baseline):
    for day in sorted(calendar):
        r = totals[day]
        collateral, debt, priced = r['collateral'], r['debt'], r['pricedCollateral']
        official = baseline.get(str(day))
        yield dict(date=str(day), estimatePct=round(100 * collateral / debt, 6) if debt else None,
                   included=r['included'], positive=r['positive'], excluded=r['excluded'], missingMargin=r['missingMargin'],
                   collateralNtd=round(collateral), debtNtd=round(debt),
                   dbCoverage=round(collateral / priced, 6) if priced else None,
                   marketCoverage=round(collateral / official, 6) if official else None,
                   seedWeight=round(r['seedDebt'] / debt, 6) if debt else None)


def rebuild(output, candle_root, baseline_file, supplement_file):
    candles = load_candles(candle_root)
    supplements = archive_gap_rows(supplement_file)
    baseline = {}
    if baseline_file is not None:
        with baseline_file.open(encoding='utf-8-sig', newline='') as f:
            baseline = {r['date']: float(r['collateral_ntd']) for r in csv.DictReader(f)}
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('Output directory must be empty')
    totals, status, model_stocks, vwap_sources = defaultdict(Counter), Counter(), set(), Counter()
    all_stocks, first, last, input_days = 0, None, None, 0
    h = sha256(MODEL.encode())
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn, \
         gzip.open(output / 'stock_days.csv.gz', 'wt', encoding='utf-8', newline='') as archive:
        conn.execute(text('SET TRANSACTION READ ONLY'))
        writer = csv.DictWriter(archive, OUTPUT_FIELDS, lineterminator='\n')
        writer.writeheader()
        # Official/FinMind date rows fill historical gaps in the calendar table.
        sessions = defaultdict(set)
        for table in (db.prices, db.margins):
            for market, day in conn.execute(select(table.c.market, table.c.trading_date).where(table.c.is_current.is_(True)).distinct()):
                sessions[market].add(day)
        for market, day in conn.execute(select(db.calendar.c.market, db.calendar.c.trading_date).where(db.calendar.c.is_open.is_(True)).distinct()):
            sessions[market].add(day)
        sessions['TWSE'].update(day for rows in supplements.values() for day in rows)
        previous = {market: dict(zip(days[1:], days[:-1])) for market, days in
                    ((market, sorted(days)) for market, days in sessions.items())}
        listings = defaultdict(list)
        for r in conn.execute(select(db.listings)).mappings():
            listings[r['security_id']].append(r)
        actions = defaultdict(lambda: defaultdict(set))
        for sid, day, kind in conn.execute(select(db.actions.c.security_id, db.actions.c.effective_date, db.actions.c.kind).where(
                db.actions.c.kind.in_(('capital_reduction', 'par_value_change', 'split_reference')))):
            if day is not None:
                actions[sid][day].add(kind)
        securities = conn.execute(select(db.securities).order_by(db.securities.c.id)).mappings().all()
        all_stocks = len(securities)
        for i, sec in enumerate(securities, 1):
            listing, = listings[sec['id']]
            market = listing['market']
            p = selected_observations(conn, db.prices, sec['id'], market)
            m = selected_observations(conn, db.margins, sec['id'], market)
            if market == 'TWSE':
                for day, (quote, balance) in supplements.get(sec['code'], {}).items():
                    p.setdefault(day, quote)
                    m.setdefault(day, balance)
            known_events = {day: '|'.join(sorted(kinds)) for day, kinds in actions[sec['id']].items()}
            for row in replay(sec, market, p, m, previous[market], known_events, candles.get(sec['code'], {})):
                day = row['date']
                # Listing end dates are exclusive; retain historic inputs in the stock archive.
                if (not listing['valid_from'] or day >= listing['valid_from']) and (not listing['valid_to'] or day < listing['valid_to']):
                    collect_market(row, totals)
                writer.writerow(row)
                h.update((json.dumps(row, default=str, separators=(',', ':'), allow_nan=False) + '\n').encode())
                first = min(first or day, day)
                last = max(last or day, day)
                input_days += 1
                status[row['status']] += 1
                if row['maintenance_pct'] is not None:
                    model_stocks.add(row['security_id'])
                    vwap_sources[row['vwap_source'] or 'not_required'] += 1
            if i % 75 == 0 or i == all_stocks:
                print(f'{i}/{all_stocks} securities, {input_days:,} stock-days', flush=True)
    market = list(market_rows(totals, sessions['TWSE'], baseline))
    summary = dict(model=MODEL, dataVersion=h.hexdigest(), stocks=all_stocks, modeledStocks=len(model_stocks),
                   stockDays=input_days, modeledStockDays=status['computed'], firstDate=str(first), lastDate=str(last),
                   dailyRows=sum(r['estimatePct'] is not None for r in market), statusCounts=status, vwapSources=vwap_sources)
    payload = dict(version=1, summary=summary, rows=market)
    (output / 'market.json').write_text(json.dumps(payload, separators=(',', ':'), allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--candles', type=Path)
    parser.add_argument('--market-baseline', type=Path)
    parser.add_argument('--official-supplement', type=Path)
    args = parser.parse_args()
    rebuild(args.output, args.candles, args.market_baseline, args.official_supplement)
