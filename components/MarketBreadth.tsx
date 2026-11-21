"use client";

import {useEffect, useRef, useState} from 'react';
import {createChart, HistogramSeries} from 'lightweight-charts';
import {chartOptions, tv} from './chartTheme';
import type {Breadth} from './Intraday';
import {base, fmt, getJSON} from './siteData';

/** Daily official limit-up / limit-down counts for listed and OTC stocks. */
export default function MarketBreadth() {
  const [data, setData] = useState<Breadth | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    getJSON<Breadth>(base + '/data/market-breadth.json').then(setData).catch(() => setData(null));
  }, []);
  const col = (row: (string | number)[], name: string) => Number(row[data!.columns.indexOf(name)]);
  useEffect(() => {
    if (!data?.rows.length || !box.current) return;
    const chart = createChart(box.current, {...chartOptions, autoSize: true, height: 260});
    chart.addSeries(HistogramSeries, {title: '跌停家數（上市＋上櫃）', color: tv.down})
      .setData(data.rows.map(row => ({time: String(row[0]), value: col(row, 'TWSE_limit_down') + col(row, 'TPEx_limit_down')})));
    chart.addSeries(HistogramSeries, {title: '漲停家數（上市＋上櫃）', color: tv.up}, 1)
      .setData(data.rows.map(row => ({time: String(row[0]), value: col(row, 'TWSE_limit_up') + col(row, 'TPEx_limit_up')})));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [data]);
  if (!data?.rows.length) return null;
  const recent = [...data.rows].reverse().slice(0, 10);
  return <section id="breadth" className="panel mt-6 overflow-hidden" aria-labelledby="breadth-title">
    <div className="space-y-2 p-4 sm:p-5">
      <h2 id="breadth-title" className="text-lg font-semibold text-white">每日漲跌停家數</h2>
      <p className="text-xs leading-5 text-slate-400">證交所「漲跌證券數合計」股票欄與櫃買中心上櫃股票當日彙總，收盤後更新。
        盤中變化見 <a className="text-teal-300" href={base + '/intraday/'}>盤中即時</a>。</p>
    </div>
    <div ref={box} aria-label="每日跌停與漲停家數"/>
    <div className="overflow-x-auto border-t border-slate-800"><table className="w-full whitespace-nowrap text-sm">
      <thead><tr>{['日期', '上市 漲停／上漲', '上市 跌停／下跌', '上櫃 漲停／上漲', '上櫃 跌停／下跌'].map(t => <th key={t}>{t}</th>)}</tr></thead>
      <tbody>{recent.map(row => <tr key={String(row[0])}>
        <td>{row[0]}</td>
        <td className="tabular-nums text-rose-300">{fmt(col(row, 'TWSE_limit_up'), 0)} / {fmt(col(row, 'TWSE_up'), 0)}</td>
        <td className="tabular-nums text-emerald-300">{fmt(col(row, 'TWSE_limit_down'), 0)} / {fmt(col(row, 'TWSE_down'), 0)}</td>
        <td className="tabular-nums text-rose-300">{fmt(col(row, 'TPEx_limit_up'), 0)} / {fmt(col(row, 'TPEx_up'), 0)}</td>
        <td className="tabular-nums text-emerald-300">{fmt(col(row, 'TPEx_limit_down'), 0)} / {fmt(col(row, 'TPEx_down'), 0)}</td>
      </tr>)}</tbody>
    </table></div>
  </section>;
}
