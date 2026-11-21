import {parseIndex, parseQuote, summarize} from './mis.js';

const MIS = 'https://mis.twse.com.tw/stock/api/getStockInfo.jsp';
const BATCH = 100;
const CLOSE_MINUTE = 275; // 13:35 Taipei

export function taipei(now = new Date()) {
  const local = new Date(now.getTime() + 8 * 3600 * 1000);
  const date = local.toISOString().slice(0, 10);
  return {date, compact: date.replaceAll('-', ''), minute: (local.getUTCHours() - 9) * 60 + local.getUTCMinutes()};
}

async function json(url, fetcher) {
  const response = await fetcher(url, {headers: {'User-Agent': 'Mozilla/5.0 (taiwan-margin-intraday)', Referer: 'https://mis.twse.com.tw/stock/index.jsp'}});
  if (!response.ok) throw new Error(`${response.status} ${url.slice(0, 80)}`);
  const text = await response.text();
  return JSON.parse(text.slice(text.indexOf('{')));
}

/** Yesterday's model state from the published site, cached once per trading day. */
export async function loadReference(env, day, fetcher = fetch) {
  const cached = await env.INTRADAY.get(`ref:${day}`, 'json');
  if (cached) return cached;
  const latest = await json(`${env.PAGES}/data/latest.json`, fetcher);
  const index = await json(`${env.PAGES}/data/snapshots/${latest.snapshot_id}/index.json`, fetcher);
  const market = await json(`${env.PAGES}/data/wantgoo-market.json`, fetcher);
  const baseline = await json(`${env.PAGES}/data/intraday-baseline.json`, fetcher).catch(() => null);
  const stocks = {};
  for (const s of index.stocks) {
    stocks[s.code] = {sid: s.security_id, name: s.name, market: s.market, ratio: s.latest?.maintenance == null ? null : Number(s.latest.maintenance),
      close: s.latest?.close == null ? null : Number(s.latest.close), balance: s.latest?.balance ?? null};
  }
  const lastMarket = market.rows.findLast(row => row[1] != null);
  const ref = {snapshot: latest.snapshot_id, asOf: lastMarket?.[0] ?? null, market: lastMarket?.[1] ?? null,
    stocks, baseline: baseline?.baseline ?? null};
  await env.INTRADAY.put(`ref:${day}`, JSON.stringify(ref), {expirationTtl: 3 * 86400});
  return ref;
}

export async function collect(env, now = new Date(), fetcher = fetch) {
  const clock = taipei(now);
  if (clock.minute < 0 || clock.minute > CLOSE_MINUTE) return {skipped: 'outside session'};
  const ref = await loadReference(env, clock.date, fetcher);
  const symbols = Object.entries(ref.stocks).map(([code, s]) => `${s.market === 'TPEx' ? 'otc' : 'tse'}_${code}.tw`);
  const batches = [['tse_t00.tw', 'otc_o00.tw', ...symbols.slice(0, BATCH - 2)]];
  for (let i = BATCH - 2; i < symbols.length; i += BATCH) batches.push(symbols.slice(i, i + BATCH));
  const rows = [];
  for (const [i, batch] of batches.entries()) {
    if (i) await new Promise(resolve => setTimeout(resolve, 800)); // stay well under MIS rate limits
    const body = await json(`${MIS}?ex_ch=${batch.join('|')}&json=1&delay=0&_=${now.getTime()}`, fetcher);
    rows.push(...(body.msgArray ?? []));
    // The first batch carries the index; on a holiday its date is not today, so stop after one request.
    if (!i && rows.find(row => row.c === 't00')?.d !== clock.compact) return {skipped: 'no session today'};
  }
  const indexRow = rows.find(row => row.c === 't00');
  const quotes = rows.filter(row => row.c !== 't00' && row.c !== 'o00' && row.d === clock.compact).map(parseQuote);
  const summary = summarize({quotes, index: parseIndex(indexRow), ref, baseline: ref.baseline, minute: clock.minute});
  const otc = rows.find(row => row.c === 'o00');
  const point = {time: indexRow.t, ...summary, otc: otc ? Number(otc.z) : null, otcPrev: otc ? Number(otc.y) : null};
  const key = `day:${clock.date}`;
  const series = (await env.INTRADAY.get(key, 'json')) ?? [];
  const {watch, ...compact} = point;
  if (series.at(-1)?.time !== compact.time) series.push(compact);
  await env.INTRADAY.put(key, JSON.stringify(series), {expirationTtl: 30 * 86400});
  await env.INTRADAY.put('latest', JSON.stringify({date: clock.date, updated: now.toISOString(), refAsOf: ref.asOf,
    universe: quotes.length, point}));
  return {stored: point.time, stocks: quotes.length};
}

export async function serve(request, env) {
  const headers = {'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': env.ALLOW_ORIGIN,
    'Cache-Control': 'public, max-age=30'};
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.pathname !== '/intraday.json') return new Response('Not found', {status: 404, headers});
  const latest = await env.INTRADAY.get('latest', 'json');
  const series = latest ? (await env.INTRADAY.get(`day:${latest.date}`, 'json')) ?? [] : [];
  return new Response(JSON.stringify({latest, series, source: '臺灣證券交易所基本市況報導網站（非交易用途，約 1 分鐘延遲）'}), {headers});
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(collect(env, new Date(controller.scheduledTime)).then(
      result => console.log(JSON.stringify(result)),
      error => { console.error(String(error)); throw error; }));
  },
  fetch: serve,
};
