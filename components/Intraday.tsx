"use client";

import {useEffect, useRef, useState} from 'react';
import {createChart, HistogramSeries, LineSeries, type UTCTimestamp} from 'lightweight-charts';
import {chartOptions, tv} from './chartTheme';
import {dropText, dropTo} from './dropTo';
import {base, fmt, getJSON, intradayUrl} from './siteData';

type Watch = {code: string; sid: number | null; name: string; ratio: number; change: number | null; lockedDown: boolean};
type Point = {
  time: string; minute: number; stocks: number; limitUp: number; limitDown: number; lockedUp: number; lockedDown: number;
  noBid: number; depthRatio: number | null; spreadTicks: number | null; pace: number | null;
  index: number | null; indexPrev: number | null; otc: number | null; otcPrev: number | null; marketRatio: number | null;
  watch?: Watch[];
};
type Payload = {
  latest: {date: string; updated: string; refAsOf: string | null; universe: number; point: Point} | null;
  series: Point[]; source: string;
};
export type Breadth = {columns: string[]; rows: (string | number)[][]};

const pct = (now: number | null, prev: number | null) =>
  now == null || !prev ? '—' : `${now >= prev ? '+' : ''}${fmt((now / prev - 1) * 100, 2)}%`;
const toTime = (date: string, time: string) => Math.floor(Date.parse(`${date}T${time}+08:00`) / 1000) as UTCTimestamp;

function SeriesChart({date, series}: {date: string; series: Point[]}) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!box.current || series.length < 2) return;
    const chart = createChart(box.current, {
      ...chartOptions, autoSize: true, height: 420,
      timeScale: {...chartOptions.timeScale, timeVisible: true, secondsVisible: false},
    });
    const at = (p: Point) => toTime(date, p.time);
    chart.addSeries(LineSeries, {title: '大盤估計維持率 %', color: '#55e1ca', lineWidth: 2})
      .setData(series.filter(p => p.marketRatio != null).map(p => ({time: at(p), value: p.marketRatio!})));
    chart.addSeries(LineSeries, {title: '成交節奏 %', color: '#f4bf62', lineWidth: 1}, 1)
      .setData(series.filter(p => p.pace != null).map(p => ({time: at(p), value: p.pace!})));
    chart.addSeries(HistogramSeries, {title: '跌停（股票池）', color: tv.down}, 2)
      .setData(series.map(p => ({time: at(p), value: p.limitDown})));
    chart.addSeries(LineSeries, {title: '跌停鎖死', color: '#c4a5ff', lineWidth: 2}, 2)
      .setData(series.map(p => ({time: at(p), value: p.lockedDown})));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [date, series]);
  return <div ref={box} className="overflow-hidden rounded border border-[#2a2e39]" aria-label="盤中估計維持率、成交節奏與跌停家數走勢"/>;
}

/** Live intraday monitor served by the Cloudflare Worker, plus the official after-close breadth. */
export default function Intraday() {
  const [data, setData] = useState<Payload | null>(null);
  const [breadth, setBreadth] = useState<Breadth | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    getJSON<Breadth>(base + '/data/market-breadth.json').then(setBreadth).catch(() => setBreadth(null));
    if (!intradayUrl) return;
    let active = true;
    const load = () => getJSON<Payload>(intradayUrl + '/intraday.json')
      .then(payload => { if (active) { setData(payload); setError(''); } })
      .catch(() => { if (active) setError('盤中服務暫時無法連線，稍後自動重試。'); });
    load();
    const timer = setInterval(load, 60_000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  const point = data?.latest?.point;
  const official = breadth?.rows.at(-1);
  const col = (name: string) => official ? Number(official[breadth!.columns.indexOf(name)]) : null;
  const cards: [string, string, string][] = point ? [
    ['大盤估計維持率', point.marketRatio == null ? '—' : fmt(point.marketRatio) + '%',
      point.marketRatio == null ? '' : `到 130%：${dropText(dropTo(point.marketRatio, 130))}`],
    ['加權／櫃買', `${pct(point.index, point.indexPrev)} / ${pct(point.otc, point.otcPrev)}`, `加權 ${fmt(point.index, 0)}`],
    ['成交節奏', point.pace == null ? '—' : fmt(point.pace, 0) + '%', '相對近 20 日同時間成交量中位數'],
    ['委買／委賣深度', point.depthRatio == null ? '—' : fmt(point.depthRatio, 2) + ' 倍',
      `無買單 ${point.noBid} 檔 · 價差中位數 ${point.spreadTicks ?? '—'} 檔`],
    ['跌停（股票池）', `${point.limitDown} 檔`, `其中鎖死 ${point.lockedDown} 檔（沒有買單）`],
    ['漲停（股票池）', `${point.limitUp} 檔`, `其中鎖死 ${point.lockedUp} 檔`],
  ] : [];

  return <main className="mx-auto max-w-7xl px-4 py-8 sm:py-10">
    <a className="text-sm text-teal-300 hover:underline" href={base + '/'}>← 台股融資維持率首頁</a>
    <p className="mt-7 text-xs font-semibold tracking-widest text-teal-300">TAIWAN MARGIN MONITOR</p>
    <h1 className="mt-2 text-3xl font-semibold text-white">盤中即時：流動性與漲跌停</h1>
    <p className="mt-2 text-sm leading-6 text-slate-400">
      交易日 09:00～13:30 每分鐘更新，約 1 分鐘延遲，非交易用途。股票池為網站追蹤的 500 檔；維持率用昨日模型值依現價推估，假設盤中融資張數不變。
    </p>
    {!intradayUrl && <p role="status" className="mt-5 rounded border border-amber-400/50 p-3 text-sm text-amber-200">
      盤中服務尚未啟用（Cloudflare Worker 部署後開放）。</p>}
    {error && <p role="alert" className="mt-5 text-rose-300">{error}</p>}
    {intradayUrl && data && !point && <p role="status" className="mt-5 text-slate-400">今天尚無盤中資料（非交易日或尚未開盤）。</p>}

    {point && data?.latest && <>
      <p className="mt-5 text-xs text-slate-400">{data.latest.date} {point.time} · 股票池 {point.stocks} 檔 · 模型基準日 {data.latest.refAsOf}</p>
      <div className="my-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
        {cards.map(([label, value, note]) => <div key={label} className="panel p-4">
          <p className="text-xs text-slate-400">{label}</p>
          <p className="mt-1 text-xl font-semibold tabular-nums text-white">{value}</p>
          <p className="mt-1 text-xs text-slate-400">{note}</p>
        </div>)}
      </div>
      <SeriesChart date={data.latest.date} series={data.series}/>
      <section className="panel mt-6 overflow-hidden" aria-labelledby="live-watch">
        <div className="p-4 sm:p-5">
          <h2 id="live-watch" className="text-lg font-semibold text-white">盤中估計維持率低於 130% 的融資股（{point.watch?.length ?? 0} 檔）</h2>
          <p className="mt-1 text-xs text-slate-400">跌停鎖死代表賣單排在跌停價、沒有買單，被追繳或斷頭的部位可能賣不掉。</p>
        </div>
        <div className="overflow-x-auto"><table className="w-full whitespace-nowrap text-sm">
          <thead><tr>{['股票', '估計維持率', '今日漲跌', '到 120% 需跌', '狀態'].map(t => <th key={t}>{t}</th>)}</tr></thead>
          <tbody>{(point.watch ?? []).map(w => <tr key={w.code}>
            <td><a className="text-teal-300 hover:underline" href={w.sid == null ? `${base}/stocks/${w.code}/` : `${base}/stocks/s-${w.sid}/`}>{w.code} {w.name}</a></td>
            <td className="tabular-nums text-white">{fmt(w.ratio)}%</td>
            <td className={'tabular-nums ' + ((w.change ?? 0) < 0 ? 'text-emerald-300' : 'text-rose-300')}>
              {w.change == null ? '—' : `${w.change > 0 ? '+' : ''}${fmt(w.change, 1)}%`}</td>
            <td className="tabular-nums">{dropText(dropTo(w.ratio, 120))}</td>
            <td>{w.lockedDown ? <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-200">跌停鎖死</span> : '—'}</td>
          </tr>)}</tbody>
        </table></div>
      </section>
    </>}

    {official && <section className="panel mt-6 p-4 sm:p-5" aria-labelledby="official-breadth">
      <h2 id="official-breadth" className="text-lg font-semibold text-white">收盤後官方漲跌停家數 · {official[0]}</h2>
      <dl className="mt-3 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
        <div><dt className="text-slate-400">上市 漲停／上漲</dt><dd className="tabular-nums text-rose-300">{col('TWSE_limit_up')} / {col('TWSE_up')}</dd></div>
        <div><dt className="text-slate-400">上市 跌停／下跌</dt><dd className="tabular-nums text-emerald-300">{col('TWSE_limit_down')} / {col('TWSE_down')}</dd></div>
        <div><dt className="text-slate-400">上櫃 漲停／上漲</dt><dd className="tabular-nums text-rose-300">{col('TPEx_limit_up')} / {col('TPEx_up')}</dd></div>
        <div><dt className="text-slate-400">上櫃 跌停／下跌</dt><dd className="tabular-nums text-emerald-300">{col('TPEx_limit_down')} / {col('TPEx_down')}</dd></div>
      </dl>
      <p className="mt-3 text-xs text-slate-400">來源：證交所「漲跌證券數合計」股票欄、櫃買中心上櫃股票當日彙總。歷史走勢見
        {' '}<a className="text-teal-300" href={base + '/market/#breadth'}>大盤頁</a>。</p>
    </section>}
    <p className="mt-6 text-xs text-slate-500">{data?.source ?? '盤中資料來源：臺灣證券交易所基本市況報導網站。'}</p>
  </main>;
}
