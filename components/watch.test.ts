import {test} from 'node:test';
import assert from 'node:assert/strict';
import {watchList} from './watch.ts';
import {marketSummary} from './marketSummary.ts';

const day = '2026-11-16';

function stock(code: string, maintenance: number | null, prior: {d1?: number | null; d5?: number | null} = {},
               extra: {market?: string; date?: string; quality?: string} = {}) {
  return {
    security_id: Number(code), code, name: 'S' + code, market: extra.market ?? 'TWSE', rank: 1,
    latest_quote_date: day, latest_estimate_date: day,
    latest: {date: extra.date ?? day, maintenance: maintenance == null ? null : String(maintenance),
             quality: extra.quality ?? 'estimated', balance: 100} as never,
    ...(prior.d1 === undefined ? {} : {maintenance_1d_ago: prior.d1 == null ? null : String(prior.d1)}),
    ...(prior.d5 === undefined ? {} : {maintenance_5d_ago: prior.d5 == null ? null : String(prior.d5)}),
  };
}

const list = (...stocks: ReturnType<typeof stock>[]) =>
  watchList({stocks, official_dates: {TWSE: day, TPEx: day}} as never);

test('below 120 is low; exactly 120 is not', () => {
  assert.deepEqual(list(stock('1001', 119.99)).map(item => item.reasons), [['low']]);
  assert.equal(list(stock('1002', 120)).length, 0);
});

test('one-day drop of exactly 15 points is flagged, 14.99 is not', () => {
  assert.deepEqual(list(stock('1001', 160, {d1: 175}))[0].reasons, ['drop1']);
  assert.equal(list(stock('1002', 160, {d1: 174.99})).length, 0);
});

test('five-day drop of exactly 15 points is flagged', () => {
  assert.deepEqual(list(stock('1001', 160, {d5: 175}))[0].reasons, ['drop5']);
});

test('all reasons combine and changes are reported', () => {
  const [item] = list(stock('1001', 110, {d1: 125, d5: 130}));
  assert.deepEqual(item.reasons, ['low', 'drop1', 'drop5']);
  assert.equal(item.change1, -15);
  assert.equal(item.change5, -20);
});

test('stale rows and markets without an official date are excluded', () => {
  assert.equal(list(stock('1001', 110, {}, {date: '2026-11-13'})).length, 0);
  const stocks = [stock('1002', 110, {}, {market: 'TPEx'})];
  assert.equal(watchList({stocks, official_dates: {TWSE: day, TPEx: null}} as never).length, 0);
});

test('missing prior fields still allow a low-level entry', () => {
  const [item] = list(stock('1001', 110));
  assert.deepEqual(item.reasons, ['low']);
  assert.equal(item.change1, null);
  assert.equal(item.change5, null);
});

test('rows without a maintenance value are excluded', () => {
  assert.equal(list(stock('1001', null, {d1: 200})).length, 0);
});

test('low-level stocks come first by level, then drops by size', () => {
  const order = list(stock('3000', 170, {d5: 190}), stock('1150', 115), stock('1050', 105)).map(item => item.stock.code);
  assert.deepEqual(order, ['1050', '1150', '3000']);
});

test('warmup quality is surfaced', () => {
  assert.equal(list(stock('1001', 110, {}, {quality: 'warmup'}))[0].warmup, true);
  assert.equal(list(stock('1001', 110))[0].warmup, false);
});

test('market summary uses the last two estimates', () => {
  assert.deepEqual(marketSummary([['a', 140, null], ['b', 150, null]]),
    {date: 'b', value: 150, change: 10, spark: [140, 150]});
});

test('market summary with one row has no change', () => {
  assert.equal(marketSummary([['a', 140]])?.change, null);
});

test('market summary skips a trailing null estimate', () => {
  assert.deepEqual(marketSummary([['a', 140], ['b', 150], ['c', null]]),
    {date: 'b', value: 150, change: 10, spark: [140, 150]});
});

test('market summary without estimates is null', () => {
  assert.equal(marketSummary([['a', null]]), null);
});

test('market spark keeps the last 60 estimates', () => {
  const rows = Array.from({length: 70}, (_, i) => [String(i), 100 + i] as [string, number]);
  const summary = marketSummary(rows);
  assert.equal(summary?.spark.length, 60);
  assert.equal(summary?.spark[0], 110);
});
