"use client";

import {useEffect, useMemo, useRef, useState} from 'react';
import {CandlestickSeries, ColorType, createChart, HistogramSeries, LineSeries, LineStyle, type Time} from 'lightweight-charts';
import {base, csvDownload, fmt, labels, loadHistory, loadIndex, type Day, type Manifest, type Stock} from './siteData';

const ranges = {'1M': 1, '3M': 3, '6M': 6, '1Y': 12, '3Y': 36, 'ALL': 0} as const;
const dayLabel = (time: Time) => typeof time === 'string' ? time :
  typeof time === 'number' ? new Date(time * 1000).toISOString().slice(0, 10) :
  `${time.year}-${String(time.month).padStart(2, '0')}-${String(time.day).padStart(2, '0')}`;

function Charts({rows, onDay}: {rows: Day[]; onDay: (row: Day | null) => void}) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current || !rows.length) return;
    const chart = createChart(container.current, {
      autoSize: true, height: 700,
      layout: {background: {type: ColorType.Solid, color: '#101a28'}, textColor: '#b5c6d8'},
      grid: {vertLines: {color: '#253244'}, horzLines: {color: '#253244'}},
      rightPriceScale: {minimumWidth: 76},
    });
    const candles = chart.addSeries(CandlestickSeries, {title: '股價'});
    candles.setData(rows.map(row => row.open != null && row.high != null && row.low != null && row.close != null
      ? {time: row.date, open: Number(row.open), high: Number(row.high), low: Number(row.low), close: Number(row.close)}
      : {time: row.date}));
    const reference = chart.addSeries(LineSeries, {
      title: '130% 參考線', color: '#f4bf62', lineStyle: LineStyle.Dashed, lineWidth: 1,
      crosshairMarkerVisible: false, priceLineVisible: false,
    }, 1);
    reference.setData(rows.map(row => ({time: row.date, value: 130})));
    for (const [key, title, color, pane] of [
      ['cost', '融資成本', '#74c5ff', 0], ['maintenance', '維持率', '#55e1ca', 1],
    ] as const) {
      // A separate series per contiguous run keeps missing dates and breaks visible.
      let run: {time: string; value: number}[] = [];
      let segment: number | null = null;
      const flush = () => {
        if (!run.length) return;
        const line = chart.addSeries(LineSeries, {
          title, color, priceLineVisible: false, lastValueVisible: false,
          pointMarkersVisible: run.length === 1,
        }, pane);
        line.setData(run);
        run = [];
      };
      for (const row of rows) {
        if (row[key] === null || row.segment !== segment) flush();
        if (row[key] !== null) run.push({time: row.date, value: Number(row[key])});
        segment = row.segment;
      }
      flush();
    }
    const balance = chart.addSeries(HistogramSeries, {title: '融資餘額', color: '#5da6d6'}, 2);
    balance.setData(rows.map(row => row.balance == null ? {time: row.date} : {time: row.date, value: row.balance}));
    const volume = chart.addSeries(HistogramSeries, {title: '成交股數', color: '#6d8aa2'}, 3);
    volume.setData(rows.map(row => row.volume == null ? {time: row.date} : {time: row.date, value: row.volume}));
    chart.panes()[0].setStretchFactor(2);
    const byDate = new Map(rows.map(row => [row.date, row]));
    function selectDay(time: Time | undefined) {
      if (time !== undefined) onDay(byDate.get(dayLabel(time)) || null);
    }
    chart.subscribeCrosshairMove(event => selectDay(event.time));
    chart.subscribeClick(event => selectDay(event.time));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [rows, onDay]);
  return <div ref={container} aria-label="股價、維持率、融資餘額及成交量圖表"/>;
}

export default function StockDetail({id}: {id: string}) {
  const [data, setData] = useState<{manifest: Manifest; stock: Stock; items: Day[]} | null>(null);
  const [error, setError] = useState('');
  const [range, setRange] = useState<keyof typeof ranges>('1Y');
  const [point, setPoint] = useState<Day | null>(null);
  const [page, setPage] = useState(1);
  useEffect(() => {
    let active = true;
    async function load() {
      const version = new URLSearchParams(window.location.search).get('snapshot') || undefined;
      const manifest = await loadIndex(version);
      const matches = manifest.stocks.filter(stock => id.startsWith('s-')
        ? id === 's-' + stock.security_id : id === stock.code);
      if (matches.length !== 1) throw new Error('找不到唯一證券身分，請從股票池重新選擇。');
      const history = await loadHistory(manifest.snapshot_id, matches[0].security_id);
      if (history.snapshot_id !== manifest.snapshot_id) throw new Error('股價與維持率版本不符。');
      if (active) setData({manifest, stock: history.stock, items: history.items});
    }
    load().catch((error: Error) => {if (active) setError(error.message);});
    return () => {active = false;};
  }, [id]);
  const rows = useMemo(() => {
    if (!data?.items.length || !ranges[range]) return data?.items || [];
    const end = new Date(data.items.at(-1)!.date + 'T00:00:00Z');
    const day = end.getUTCDate();
    end.setUTCDate(1);
    end.setUTCMonth(end.getUTCMonth() - ranges[range]);
    const lastDay = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() + 1, 0)).getUTCDate();
    end.setUTCDate(Math.min(day, lastDay));
    const start = end.toISOString().slice(0, 10);
    return data.items.filter(row => row.date >= start);
  }, [data, range]);
  const reversed = useMemo(() => [...rows].reverse(), [rows]);
  const selected = point || rows.at(-1);
  const quality = (row?: Day) => row ? labels[row.quality] || row.quality : '無可用資料';

  return <main className="mx-auto max-w-6xl px-4 py-8">
    <a className="text-teal-300" href={base + '/'}>← 股票池</a>
    {error && <p role="alert" className="mt-5 text-rose-300">{error} <a className="underline" href={base + '/stocks/' + encodeURIComponent(id) + '/'}>查看目前版本</a></p>}
    {!data && !error && <p role="status" className="mt-5">載入個股歷史…</p>}
    {data && <>
      <h1 className="mt-5 text-3xl font-semibold text-white">{data.stock.code} {data.stock.name}</h1>
      <p className="my-3 text-sm text-slate-400">
        行情 {data.stock.latest_quote_date || '—'} · 最後可估算 {data.stock.latest_estimate_date || '—'} · {data.manifest.model} · {data.manifest.snapshot_id.slice(0, 12)}
      </p>
      <p className="mb-5 text-sm text-slate-400">收盤價模型，假設融資成數 60%，未校準。130% 為模型參考線。</p>
      <div className="panel mb-5 p-4">
        <div className="flex flex-wrap gap-2" aria-label="日期區間">
          {(Object.keys(ranges) as (keyof typeof ranges)[]).map(key =>
            <button key={key} aria-pressed={range === key} className={range === key ? 'border-teal-400 text-teal-200' : ''}
              onClick={() => {setRange(key); setPoint(null); setPage(1);}}>{key}</button>)}
        </div>
        <p className="my-4 text-sm text-slate-300" aria-live="off">
          {selected?.date || '—'} · 開 {fmt(selected?.open)} · 高 {fmt(selected?.high)} · 低 {fmt(selected?.low)} · 收 {fmt(selected?.close)} ·
          成本 {fmt(selected?.cost)} · 維持率 {selected?.maintenance == null ? '—' : fmt(selected.maintenance) + '%'} ·
          130% 參考價 {fmt(selected?.reference_130)} · 餘額 {fmt(selected?.balance)} · 成交量 {fmt(selected?.volume)} · {quality(selected)}
          {selected?.action && ` · ${selected.action.reason || (selected.action.cash ? '每股現金 ' + selected.action.cash : selected.action.kind)}`}
        </p>
        {rows.length ? <Charts rows={rows} onDay={setPoint}/> : <p className="text-amber-200">此期間缺少可用歷史資料。</p>}
      </div>
      <section className="panel overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 p-4">
          <h2 className="font-semibold text-white">逐日資料 · {rows.length} 日</h2>
          <button disabled={!rows.length} onClick={() => csvDownload(`${data.stock.code}-${range}.csv`, [
            ['日期', '開盤', '最高', '最低', '收盤', '成交股數', '融資餘額', '推估成本', '維持率%', '130%參考價',
              '成本段', '品質', '行情來源', '融資來源', '公司行動', '股利', '快照', '模型'],
            ...rows.map(row => [row.date, row.open, row.high, row.low, row.close, row.volume, row.balance, row.cost,
              row.maintenance, row.reference_130, row.segment, row.quality, row.price_source, row.margin_source,
              row.action?.kind, row.action?.cash, data.manifest.snapshot_id, data.manifest.model]),
          ])}>下載所選期間 CSV</button>
        </div>
        <div className="overflow-x-auto"><table className="w-full whitespace-nowrap">
          <thead><tr>{['日期','收盤','推估成本','維持率','130% 參考價','融資餘額','成本段','資料來源','品質'].map(title => <th key={title}>{title}</th>)}</tr></thead>
          <tbody>{reversed.slice((page - 1) * 30, page * 30).map(row => <tr key={row.date}>
            <td>{row.date}</td><td>{fmt(row.close)}</td><td>{fmt(row.cost)}</td>
            <td>{row.maintenance == null ? '—' : fmt(row.maintenance) + '%'}</td><td>{fmt(row.reference_130)}</td>
            <td>{fmt(row.balance)}</td><td>{row.segment ?? '—'}</td><td>{row.price_source || '—'} / {row.margin_source || '—'}</td>
            <td className="text-xs text-amber-200">{quality(row)}{row.warmup && row.quality !== 'warmup' ? ' · 暖機' : ''}</td>
          </tr>)}</tbody>
        </table></div>
        <div className="flex justify-end gap-3 p-4 text-sm">
          <button disabled={page <= 1} onClick={() => setPage(page - 1)}>上一頁</button>
          <span>第 {page} 頁</span>
          <button disabled={page * 30 >= rows.length} onClick={() => setPage(page + 1)}>下一頁</button>
        </div>
      </section>
    </>}
  </main>;
}
