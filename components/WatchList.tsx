"use client";

import {useState} from 'react';
import {change} from './MarketCard';
import {fmt, labels, stockLink, type Manifest} from './siteData';
import {WATCH, watchList, type WatchReason} from './watch';

const reasonText: Record<WatchReason, string> = {
  low: `低於 ${WATCH.low}%`,
  drop1: '單日急跌',
  drop5: '5 日急跌',
};
const preview = 20;

export default function WatchList({data}: {data: Manifest}) {
  const [expanded, setExpanded] = useState(false);
  const items = watchList(data);
  const visible = expanded ? items : items.slice(0, preview);
  return <section className="panel mb-5 overflow-hidden" aria-labelledby="watch-title">
    <div className="p-4 sm:p-5">
      <h2 id="watch-title" className="text-lg font-semibold text-white">今日該注意（{items.length} 檔）</h2>
      <p className="mt-1 text-xs leading-5 text-slate-400">
        低於 {WATCH.low}%，或單日跌 ≥{WATCH.drop1}、5 日跌 ≥{WATCH.drop5} 個百分點。僅含最新交易日的個股模型估算，未校準，不代表實際追繳。
      </p>
    </div>
    {items.length === 0
      ? <p className="px-5 pb-5 text-sm text-slate-400">今日沒有符合條件的股票。</p>
      : <div className="overflow-x-auto"><table className="w-full whitespace-nowrap">
        <thead><tr>{['股票', '維持率', '單日', '5 日', '條件', '品質', '融資餘額'].map(title => <th key={title}>{title}</th>)}</tr></thead>
        <tbody>{visible.map(item => <tr key={item.stock.security_id}>
          <td><a className="text-teal-300 underline-offset-4 hover:underline" href={stockLink(item.stock, data.snapshot_id)}>
            {item.stock.code} {item.stock.name}</a></td>
          <td className="tabular-nums text-white">{fmt(item.maintenance)}%</td>
          <td className="tabular-nums">{change(item.change1)}</td>
          <td className="tabular-nums">{change(item.change5)}</td>
          <td><span className="flex gap-1">{item.reasons.map(reason =>
            <span key={reason} className={'rounded px-2 py-0.5 text-xs ' +
              (reason === 'low' ? 'bg-rose-500/15 text-rose-200' : 'bg-amber-500/15 text-amber-200')}>{reasonText[reason]}</span>)}
          </span></td>
          <td>{item.warmup
            ? <span className="rounded border border-amber-400/60 px-2 py-0.5 text-xs text-amber-200">暖機估算</span>
            : <span className="text-xs text-slate-400">{labels[item.stock.latest?.quality || ''] || '待查'}</span>}</td>
          <td className="tabular-nums">{fmt(item.stock.latest?.balance, 0)}</td>
        </tr>)}</tbody>
      </table></div>}
    {items.length > preview && <div className="flex justify-end p-4">
      <button type="button" onClick={() => setExpanded(!expanded)}>{expanded ? '收合' : `顯示全部 ${items.length} 檔`}</button>
    </div>}
  </section>;
}
