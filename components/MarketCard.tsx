"use client";

import {useEffect, useState} from 'react';
import {base, fmt, getJSON} from './siteData';
import {marketSummary, type MarketSummary} from './marketSummary';

export {marketSummary};

function Spark({points}: {points: number[]}) {
  if (points.length < 2) return null;
  const low = Math.min(...points), high = Math.max(...points), span = high - low || 1;
  const path = points.map((value, i) =>
    `${(i / (points.length - 1) * 200).toFixed(1)},${(38 - (value - low) / span * 36).toFixed(1)}`).join(' ');
  return <svg viewBox="0 0 200 40" preserveAspectRatio="none" className="h-12 w-full max-w-xs" role="img"
    aria-label={`近 ${points.length} 個交易日走勢，最低 ${fmt(low)}%，最高 ${fmt(high)}%`}>
    <polyline points={path} fill="none" stroke="#55e1ca" strokeWidth="1.5" vectorEffect="non-scaling-stroke"/>
  </svg>;
}

export function change(value: number | null) {
  if (value == null) return '—';
  return `${value < 0 ? '▼' : value > 0 ? '▲' : ''}${fmt(Math.abs(value))}`;
}

/** Listed-market margin ratio; hides itself when the market file cannot be loaded. */
export default function MarketCard() {
  const [summary, setSummary] = useState<MarketSummary | null>(null);
  useEffect(() => {
    getJSON<{rows: [string, number | null][]}>(base + '/data/wantgoo-market.json')
      .then(payload => setSummary(marketSummary(payload.rows))).catch(() => setSummary(null));
  }, []);
  if (!summary) return null;
  return <a href={base + '/market/'} className="panel mb-5 flex flex-wrap items-center justify-between gap-4 p-5 hover:border-teal-400">
    <div>
      <p className="text-xs text-slate-400">上市大盤融資維持率 · {summary.date}</p>
      <p className="mt-1 text-3xl font-semibold tabular-nums text-white">{fmt(summary.value)}%</p>
      <p className="mt-1 text-sm tabular-nums text-slate-300">
        較前一交易日 {change(summary.change)}{summary.change == null ? '' : ' 個百分點'}
      </p>
    </div>
    <div className="flex min-w-[12rem] flex-1 flex-col items-end gap-1">
      <Spark points={summary.spark}/>
      <span className="text-sm text-teal-300">查看大盤與跌破門檻回測 →</span>
    </div>
  </a>;
}
