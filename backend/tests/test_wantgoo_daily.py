from copy import deepcopy
import json

import pytest

from app.site_snapshot import write_seed
from app.wantgoo_daily import START, advance, export, step


def observed(balance=100):
    return {'previous_balance': 100, 'margin_buy': 20, 'margin_sell': 100 + 20 - balance,
            'repayment': 0, 'balance': balance, 'close': '120', 'low': '110', 'high': '120',
            'volume': 10000, 'turnover': 1200000}


def test_previous_cost_rolls_forward_and_zero_inventory_resets():
    cost, weight, lots = step([100., .5, 100], observed())
    assert (cost, weight, lots) == (104., .4, 100)
    assert step([cost, weight, lots], {'balance': 0}) == [None, None, 0]
    assert step([100., .5, 100], {**observed(), 'turnover': 0}) == [None, None, 100]


def test_daily_aggregate_is_idempotent_and_reference_does_not_set_cost():
    day = '2026-11-10'
    stock = observed()
    state = {'through': START, 'rows': [], 'positions': {'2330': [100., .5, 100]}, 'references': {}}
    seed = {'wantgoo_daily': state, 'stocks': [{'code': '2330', 'market': 'TWSE', 'rows': {day: stock}}]}
    quotes, margins = {code: stock for code in ('2330', '0050', '2454')}, {code: stock for code in ('2330', '0050', '2454')}
    advance(seed, day, quotes, margins)
    assert state['rows'][0][1] == pytest.approx(100 * 120 / (104 * .6), abs=1e-6)
    assert state['rows'][0][6] == .5  # ETF is outside the comparison scope.
    prior = deepcopy(seed)
    advance(seed, day, quotes, margins)
    assert prior == seed


def test_export_appends_daily_rows_without_wantgoo_reference(tmp_path):
    base_version = 'a' * 64
    row = [START, 140., 139.4, 1, 1, 1., .9, .0, 0, 100, 71]
    history = {'version': 2, 'rebuild': {'dataVersion': base_version, 'lastDate': START, 'dailyRows': 1}, 'rows': [row]}
    daily = ['2026-11-10', 146.5, None, 1, 1, 1., .9, .01, 0, 105, 72]
    # A legacy checkpoint still carrying a reference map must not leak it into the export.
    state = {'format': 1, 'baseVersion': base_version, 'through': '2026-11-10', 'positions': {},
             'rows': [daily], 'references': {'2026-11-10': 145.3}}
    seed_path, historical, output = tmp_path / 'seed.json.gz', tmp_path / 'market.json', tmp_path / 'out.json'
    write_seed(seed_path, {'format': 1, 'stocks': [{}] * 500, 'wantgoo_daily': state})
    historical.write_text(json.dumps(history))
    export(seed_path, historical, output)
    payload = json.loads(output.read_text())
    assert payload['rows'] == [row, daily]
    assert payload['daily'] == {'through': '2026-11-10', 'appended': 1}
    assert payload['rebuild']['dailyRows'] == 2


def test_incomplete_inputs_do_not_mutate_cursor_or_cost_state():
    day = '2026-11-10'
    state = {'through': START, 'rows': [], 'positions': {'2330': [100., 0., 100]}}
    seed = {'wantgoo_daily': state, 'stocks': [{'code': '2330', 'market': 'TWSE', 'rows': {day: {'close': 120}}}]}
    prior = deepcopy(seed)
    with pytest.raises(ValueError, match='insufficient'):
        advance(seed, day, {}, {})
    assert seed == prior
