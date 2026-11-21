import {test} from 'node:test';
import assert from 'node:assert/strict';
import {limitState, parseQuote, summarize, baselineAt, tick} from './mis.js';
import {collect, serve, taipei} from './index.js';

const raw = (c, z, y, u, w, a, f, b, g, ex = 'tse') => ({c, ex, d: '20261120', t: '10:00:00', z, y, u, w, v: '100',
  a: a.map(x => x.toFixed(4) + '_').join(''), f: f.join('_') + (f.length ? '_' : ''), b: b.map(x => x.toFixed(4) + '_').join(''), g: g.join('_') + (g.length ? '_' : '')});

test('limit states from the order book', () => {
  // 環球晶 on 2026-11-20: traded at 441.5, limit down 441 still has buyers.
  const q = parseQuote(raw('6488', '441.5000', '490.0000', '539.0000', '441.0000', [441.5, 442.5], [9, 1], [441], [20], 'otc'));
  assert.deepEqual(limitState(q), {limitDown: false, limitUp: false, lockedDown: false, lockedUp: false});
  const locked = parseQuote(raw('1111', '-', '100', '110', '90', [90], [5000], [], []));
  assert.deepEqual(limitState(locked), {limitDown: true, limitUp: false, lockedDown: true, lockedUp: false});
  const up = parseQuote(raw('2222', '110', '100', '110', '90', [], [], [110], [800]));
  assert.equal(limitState(up).lockedUp, true);
  assert.equal(tick(441), 0.5); assert.equal(tick(2000), 5); assert.equal(tick(9.5), 0.01);
});

test('summary: counts, depth, pace and live ratios', () => {
  const quotes = [
    parseQuote(raw('1111', '-', '100', '110', '90', [90], [5000], [], [])),
    parseQuote(raw('3333', '95', '100', '110', '90', [95.1], [10], [95], [30])),
  ];
  const ref = {market: 140, stocks: {'1111': {name: 'A', ratio: 140, close: 100, balance: 1000}, '3333': {name: 'B', ratio: 150, close: 100, balance: 1000}}};
  const baseline = {minutes: [0, 60], qty: [10, 200]};
  const s = summarize({quotes, index: {volume: 300, last: 1, prev: 1}, ref, baseline, minute: 60});
  assert.equal(s.limitDown, 1); assert.equal(s.lockedDown, 1); assert.equal(s.noBid, 1);
  assert.equal(s.depthRatio, 0.01); assert.equal(s.pace, 150); assert.equal(s.spreadTicks, 1);
  // A: 140 × 90/100 = 126 (locked at limit down); B: 150 × 95/100 = 142.5. Market: 140 × 185/200.
  assert.deepEqual(s.watch, [{code: '1111', sid: null, name: 'A', ratio: 126, change: -10, lockedDown: true}]);
  assert.equal(s.marketRatio, 129.5);
  assert.equal(baselineAt(baseline, 30), 10);
});

function kv() {
  const store = new Map();
  return {store, async get(key, type) { const v = store.get(key); return v == null ? null : type === 'json' ? JSON.parse(v) : v; },
    async put(key, value) { store.set(key, value); }};
}

test('collect stores a minute point and serve returns it', async () => {
  const env = {INTRADAY: kv(), PAGES: 'https://pages', ALLOW_ORIGIN: 'https://idiego8gs.github.io'};
  const responses = {
    'https://pages/data/latest.json': {snapshot_id: 'abc'},
    'https://pages/data/snapshots/abc/index.json': {stocks: [{code: '1111', name: 'A', market: 'TWSE', latest: {maintenance: '140', close: '100', balance: 1000}}]},
    'https://pages/data/wantgoo-market.json': {rows: [['2026-11-19', 129.2]]},
    'https://pages/data/intraday-baseline.json': {baseline: {minutes: [0, 60], qty: [10, 200]}},
  };
  const fetcher = async url => {
    if (url.startsWith('https://mis.twse.com.tw')) {
      return new Response('\n\n' + JSON.stringify({msgArray: [{c: 't00', d: '20261120', t: '10:00:00', z: '36000', y: '38000', m: '300'},
        {c: 'o00', d: '20261120', t: '10:00:00', z: '263', y: '285'}, raw('1111', '-', '100', '110', '90', [90], [5000], [], [])]}));
    }
    return new Response(JSON.stringify(responses[url]));
  };
  const now = new Date('2026-11-20T02:00:00Z'); // 10:00 Taipei
  assert.equal(taipei(now).minute, 60);
  assert.deepEqual(await collect(env, now, fetcher), {stored: '10:00:00', stocks: 1});
  const body = await (await serve(new Request('https://w/intraday.json'), env)).json();
  assert.equal(body.latest.point.lockedDown, 1); assert.equal(body.series.length, 1); assert.equal(body.series[0].pace, 150);
  assert.equal(body.latest.point.watch[0].code, '1111');
  assert.deepEqual(await collect(env, new Date('2026-11-20T06:00:00Z'), fetcher), {skipped: 'outside session'});
  let misCalls = 0;
  const holiday = async url => { if (url.startsWith('https://mis')) misCalls++; return fetcher(url); };
  assert.deepEqual(await collect(env, new Date('2026-11-23T02:00:00Z'), holiday), {skipped: 'no session today'});
  assert.equal(misCalls, 1);
});
