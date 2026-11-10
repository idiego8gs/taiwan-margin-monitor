"""Continue the validated listed-stock cost vintage inside the Pages checkpoint."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from math import isfinite
from pathlib import Path
import re

from .site_snapshot import atomic_write, json_bytes, read_seed
from .wantgoo_rebuild import COST_SPLITS, FRACTION, daily_cost

START = '2026-11-09'
REFERENCE = 'https://www.wantgoo.com/stock/-ETFA/margin-trading/historical-lending-balance'
STOCK = re.compile(r'[1-9][0-9]{3}\Z')
SPLITS = {(code, str(day)): factor for (code, day), factor in COST_SPLITS.items()}


def initialize(seed, market):
    """Derive the base cost state from the already-hosted stock checkpoint."""
    if market is None:
        return
    stocks = [stock for stock in seed['stocks'] if stock['market'] == 'TWSE']
    codes = {stock['code'] for stock in stocks}
    state = seed.get('wantgoo_daily')
    if state is not None:
        if set(state['positions']) == codes and state['baseVersion'] == market['rebuild']['dataVersion']:
            return
        raise ValueError('Listing universe changed; reconcile cost state before continuing')
    if (seed['official_dates']['TWSE'] or '') < START:
        return
    if not codes or len(codes) != len(stocks) or not all(STOCK.fullmatch(code) for code in codes):
        raise ValueError('Invalid listed-stock scope')
    if market['version'] != 2 or market['rebuild']['lastDate'] != START or market['rows'][-1][0] != START:
        raise ValueError('Cost checkpoint dates do not match the published rebuild')

    dates = sorted({day for day in seed['calendar']['TWSE'] if day <= START} |
                   {day for stock in stocks for day in stock['rows'] if day <= START})
    previous = dict(zip(dates[1:], dates[:-1]))
    positions = {}
    included = 0
    collateral = debt = 0.
    for stock in stocks:
        code = stock['code']
        last = None
        position = [None, None, None]
        for day, row in sorted(stock['rows'].items()):
            if day > START:
                break
            suspended = code == '4763' and last == '2025-06-18' and day == '2025-06-30'
            if last != previous.get(day) and not suspended:
                position = [None, None, None]
            position = step(position, row, SPLITS.get((code, day), 1.))
            last = day
        if last != START:
            position = [None, None, None]
        positions[code] = position
        cost, _, lots = position
        close = stock['rows'].get(START, {}).get('close')
        if cost is not None and lots and close:
            included += 1
            collateral += lots * 1000 * float(close)
            debt += FRACTION * lots * 1000 * cost
    if included < len(codes) * .8 or not debt:
        raise ValueError('Too few positions to establish the cloud cost state')
    ratio = 100 * collateral / debt
    difference = ratio - market['rows'][-1][1]
    if abs(difference) > 1.5:
        raise ValueError(f'Cloud cost replay differs from the base curve by {difference:.3f} pp')
    print(f'Cloud cost replay {START}: {included} positions, {ratio:.6f}%, difference {difference:+.6f} pp', flush=True)
    seed['wantgoo_daily'] = {'format': 1, 'baseVersion': market['rebuild']['dataVersion'], 'through': START,
                             'positions': positions, 'rows': [], 'references': {}}


def step(previous, row, split_factor=1.):
    previous_cost, weight, _ = previous
    q = row.get('balance')
    if q is None or q == 0:
        return [None, None, q]
    inputs = [row.get(k) for k in ('previous_balance', 'margin_buy', 'margin_sell', 'repayment')]
    if any(type(v) is not int or v < 0 for v in inputs):
        return [None, None, q]
    before, buys, sells, repay = inputs
    if before + buys - sells - repay != q or not 0 <= buys <= q:
        return [None, None, q]
    volume, turnover = row.get('volume'), row.get('turnover')
    vwap = float(turnover) / float(volume) if volume and turnover else None
    low, high = row.get('low'), row.get('high')
    if vwap is not None and (vwap <= 0 or (low and vwap < float(low) * .995) or (high and vwap > float(high) * 1.005)):
        vwap = None
    if buys and vwap is None:
        return [None, None, q]
    if buys == q:
        return [daily_cost(None, q, buys, vwap), 0., q]
    retained = (q - buys) / q
    if previous_cost is not None and before > 0:
        return [daily_cost(previous_cost / split_factor, q, buys, vwap), weight * retained, q]
    if vwap is None:
        return [None, None, q]
    return [daily_cost(vwap, q, buys, vwap), retained, q]


def advance(seed, day, quotes, margins):
    state = seed.get('wantgoo_daily')
    if state is None or day <= state['through']:
        return
    positions = {}
    collateral = debt = seed_debt = priced = 0.
    positive = included = missing = 0
    for stock in seed['stocks']:
        code = stock['code']
        if stock['market'] != 'TWSE' or code not in state['positions']:
            continue
        row = stock['rows'][day]
        cost, weight, lots = positions[code] = step(state['positions'][code], row, SPLITS.get((code, day), 1.))
        if lots is None:
            missing += 1
        elif lots > 0:
            positive += 1
            price = float(row['close']) if row.get('close') else 0.
            if price > 0:
                value = lots * 1000 * price
                priced += value
                if cost is not None:
                    borrow = FRACTION * lots * 1000 * cost
                    included += 1
                    collateral += value
                    debt += borrow
                    seed_debt += borrow * weight
    if not debt or not priced or collateral / priced < .9 or included < len(positions) * .8:
        raise ValueError(f'{day}: insufficient listed-stock inputs to advance the market curve')
    full_market = sum(float(quotes[code]['close']) * row['balance'] * 1000
                      for code, row in margins.items() if STOCK.fullmatch(code) and row['balance'] > 0
                      and code in quotes and quotes[code].get('close'))
    market_coverage = collateral / full_market if full_market >= collateral else None
    state['rows'].append([day, round(100 * collateral / debt, 6), None, included, positive,
                          round(collateral / priced, 6), round(market_coverage, 6) if market_coverage else None,
                          round(seed_debt / debt, 6), missing, round(collateral), round(debt)])
    state['positions'], state['through'] = positions, day


def refresh_reference(seed, client):
    state = seed.get('wantgoo_daily')
    if state is None or state['through'] == START or state['through'] in state['references']:
        return
    try:
        response = json.loads(client.get(REFERENCE, 'Wantgoo'))
        if not isinstance(response, list) or not 1 <= len(response) <= 600:
            raise ValueError('unexpected reference shape')
        days = {row[0] for row in state['rows']}
        observations = {}
        for item in response:
            day = (datetime.fromtimestamp(item['date'] / 1000, timezone.utc) + timedelta(hours=8)).date().isoformat()
            ratio = float(item['marginRatio']) * 100
            if day in days:
                if not isfinite(ratio) or not 0 < ratio < 1000 or day in observations:
                    raise ValueError('invalid reference observation')
                observations[day] = round(ratio, 6)
        state['references'].update(observations)
    except (OSError, KeyError, TypeError, ValueError, OverflowError) as exc:
        # The independent calculation can publish while its comparison is pending.
        print(f'Wantgoo comparison unavailable ({type(exc).__name__}); retained prior observations.', flush=True)


def export(seed_path, historical_path, output):
    seed = read_seed(seed_path)
    payload = json.loads(historical_path.read_bytes())
    state = seed.get('wantgoo_daily')
    if state is not None:
        if payload['version'] != 2 or payload['rebuild']['dataVersion'] != state['baseVersion'] or payload['rows'][-1][0] != START:
            raise ValueError('Daily cost checkpoint does not match base curve')
        tail = [[row[0], row[1], state['references'].get(row[0]), *row[3:]] for row in state['rows']]
        payload['rows'].extend(tail)
        payload['rebuild']['dailyRows'] += len(tail)
        payload['daily'] = {'through': state['through'], 'appended': len(tail),
                            'referenceThrough': max(state['references'], default=START)}
    atomic_write(output, json_bytes(payload) + b'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    e = commands.add_parser('export')
    for name in ('seed', 'historical', 'output'):
        e.add_argument(f'--{name}', type=Path, required=True)
    args = parser.parse_args()
    export(args.seed, args.historical, args.output)


if __name__ == '__main__':
    main()
