// Pure helpers for TWSE MIS quotes (getStockInfo.jsp). Prices arrive as strings; "-" means none.
const num = value => {
  const n = Number(value);
  return value == null || value === '-' || value === '' || !Number.isFinite(n) ? null : n;
};
const levels = value => String(value ?? '').split('_').filter(Boolean).map(Number).filter(Number.isFinite);

/** Index rows (t00 / o00) carry the cumulative traded quantity in `m`, as in MI_5MINS. */
export function parseIndex(q) {
  return {code: q.c, date: q.d, time: q.t, last: num(q.z), prev: num(q.y), volume: num(q.m)};
}

export function parseQuote(q) {
  return {
    code: q.c, ex: q.ex, date: q.d, time: q.t,
    last: num(q.z), prev: num(q.y), up: num(q.u), down: num(q.w), volume: num(q.v),
    asks: levels(q.a), askVols: levels(q.f), bids: levels(q.b), bidVols: levels(q.g),
  };
}

// Taiwan equity tick sizes.
export function tick(price) {
  return price < 10 ? 0.01 : price < 50 ? 0.05 : price < 100 ? 0.1 : price < 500 ? 0.5 : price < 1000 ? 1 : 5;
}

const same = (a, b) => a != null && b != null && Math.abs(a - b) < 1e-6;

/** Limit state of one stock. A lock means the queue on one side is exhausted at the limit price. */
export function limitState(q) {
  const bestAsk = q.asks[0] ?? null, bestBid = q.bids[0] ?? null;
  const lockedDown = same(bestAsk, q.down) && q.bids.length === 0;
  const lockedUp = same(bestBid, q.up) && q.asks.length === 0;
  return {
    limitDown: same(q.last, q.down) || lockedDown,
    limitUp: same(q.last, q.up) || lockedUp,
    lockedDown, lockedUp,
  };
}

const median = values => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b), mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};

/** Baseline value at the given minute since 09:00 (step function over the stored minutes). */
export function baselineAt(baseline, minute, field = 'qty') {
  if (!baseline?.minutes?.length) return null;
  let value = null;
  for (let i = 0; i < baseline.minutes.length && baseline.minutes[i] <= minute; i++) value = baseline[field][i];
  return value;
}

/**
 * One-minute summary.
 * quotes: parsed stock quotes; index: parsed TWSE index quote (volume = cumulative lots);
 * ref: {stocks: {code: {ratio, close, balance, name}}, market: ratio}; minute: minutes since 09:00.
 */
export function summarize({quotes, index, ref, baseline, minute}) {
  let limitUp = 0, limitDown = 0, lockedUp = 0, lockedDown = 0, noBid = 0, bidDepth = 0, askDepth = 0;
  let weightedNow = 0, weightedPrev = 0;
  const spreads = [], watch = [];
  for (const q of quotes) {
    const state = limitState(q);
    limitUp += state.limitUp; limitDown += state.limitDown; lockedUp += state.lockedUp; lockedDown += state.lockedDown;
    if (!q.bids.length) noBid++;
    bidDepth += q.bidVols.reduce((a, b) => a + b, 0);
    askDepth += q.askVols.reduce((a, b) => a + b, 0);
    if (q.asks.length && q.bids.length) spreads.push(Math.round((q.asks[0] - q.bids[0]) / tick(q.bids[0])));
    const r = ref.stocks[q.code];
    const price = q.last ?? (state.lockedDown ? q.down : state.lockedUp ? q.up : q.prev);
    if (!r || r.ratio == null || !r.close || price == null) continue;
    const ratio = r.ratio * price / r.close;
    if (r.balance) { weightedNow += r.balance * price; weightedPrev += r.balance * r.close; }
    if (ratio < 130) {
      watch.push({code: q.code, sid: r.sid ?? null, name: r.name, ratio: Math.round(ratio * 100) / 100,
        change: q.prev ? Math.round((price / q.prev - 1) * 1000) / 10 : null, lockedDown: state.lockedDown});
    }
  }
  const expected = baselineAt(baseline, minute);
  return {
    minute, stocks: quotes.length, limitUp, limitDown, lockedUp, lockedDown, noBid,
    depthRatio: askDepth ? Math.round(bidDepth / askDepth * 100) / 100 : null,
    spreadTicks: median(spreads),
    pace: expected && index?.volume != null ? Math.round(index.volume / expected * 1000) / 10 : null,
    index: index?.last ?? null, indexPrev: index?.prev ?? null,
    marketRatio: ref.market != null && weightedPrev ? Math.round(ref.market * weightedNow / weightedPrev * 100) / 100 : null,
    watch: watch.sort((a, b) => a.ratio - b.ratio).slice(0, 60),
  };
}
