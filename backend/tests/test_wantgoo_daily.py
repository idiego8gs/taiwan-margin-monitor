from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from app.wantgoo_daily import START, advance, refresh_reference, step


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

    class Source:
        def get(self, url, market):
            assert market == 'Wantgoo'
            stamp = int(datetime(2026, 11, 9, 16, tzinfo=timezone.utc).timestamp() * 1000)
            return json.dumps([{'date': stamp, 'marginRatio': 1.42}])

    refresh_reference(seed, Source())
    assert state['references'] == {day: 142.}
    assert state['positions'] == prior['wantgoo_daily']['positions']


def test_incomplete_inputs_do_not_mutate_cursor_or_cost_state():
    day = '2026-11-10'
    state = {'through': START, 'rows': [], 'positions': {'2330': [100., 0., 100]}}
    seed = {'wantgoo_daily': state, 'stocks': [{'code': '2330', 'market': 'TWSE', 'rows': {day: {'close': 120}}}]}
    prior = deepcopy(seed)
    with pytest.raises(ValueError, match='insufficient'):
        advance(seed, day, {}, {})
    assert seed == prior
