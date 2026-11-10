"use client";

import {useEffect, useState} from 'react';
import {base, csvDownload, fmt, isPages, labels, loadIndex, stockLink, type Manifest} from '../components/siteData';

const pageSize = 50;

export default function Home() {
  const [data, setData] = useState<Manifest | null>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [market, setMarket] = useState('');
  const [page, setPage] = useState(1);
  const [order, setOrder] = useState('rank');
  useEffect(() => { loadIndex().then(setData).catch((error: Error) => setError(error.message)); }, []);

  const match = query.trim().toLowerCase();
  const rows = (data?.stocks || []).filter(stock =>
    (!market || stock.market === market) && (stock.code + ' ' + stock.name).toLowerCase().includes(match)
  );
  if (order === 'maintenance') rows.sort((a, b) =>
    Number(a.latest?.maintenance ?? Infinity) - Number(b.latest?.maintenance ?? Infinity)
  );
  const lastPage = Math.max(1, Math.ceil(rows.length / pageSize));
  const visible = rows.slice((page - 1) * pageSize, page * pageSize);
  const taipeiDate = new Date().toLocaleDateString('en-CA', {timeZone: 'Asia/Taipei'});
  const stale = data?.official_through &&
    Date.parse(taipeiDate) - Date.parse(data.official_through) > 4 * 86_400_000;

  return <main className="mx-auto max-w-7xl px-4 py-10">
    <p className="text-xs font-semibold tracking-widest text-teal-300">TAIWAN MARGIN MONITOR</p>
    <h1 className="mt-2 text-3xl font-semibold text-white">台股融資維持率</h1>
    <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-400">
      收盤價模型，假設融資成數 60%，未校準。130% 為個股模型參考線，實際追繳依投資人整戶維持率判斷。
    </p>
    <nav className="my-5 flex flex-wrap gap-4 text-sm text-teal-300">
      <a href={base + '/market/'}>大盤維持率</a>
      <a href={isPages ? base + '/research.html' : '/research'}>1～20 日回測</a>
      {!isPages && <>
        <a href="/progress">資料進度</a><a href="/basket">合併試算</a>
        <a href="/watchlist">自選股</a><a href="/sources">來源比較</a>
      </>}
    </nav>
    {error && <p role="alert" className="mb-4 text-rose-300">{error}</p>}
    {!data && !error && <p role="status" className="text-slate-400">載入資料快照…</p>}
    {data && <>
      <div className="panel mb-5 grid gap-4 p-5 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <div><b className="block text-white">股票池</b>{data.stocks.length} 檔 · {data.universe_date}</div>
        <div><b className="block text-white">各市場資料日期</b>上市 {data.official_dates.TWSE || '待核對'}<br/>上櫃 {data.official_dates.TPEx || '待核對'}</div>
        <div><b className="block text-white">快照產生時間</b>{new Date(data.created_at).toLocaleString('zh-TW', {timeZone: 'Asia/Taipei'})}（台北）<br/>{data.snapshot_id.slice(0, 12)}</div>
        <div><b className="block text-white">最新日待查</b>{data.review_count} 檔<br/>
          {data.workflow_run
            ? <a className="text-teal-300" href={data.workflow_run}>發布流程紀錄</a>
            : '歷史快照 · 雲端更新待驗證'}
        </div>
      </div>
      {stale && <p role="status" className="mb-4 text-sm text-amber-200">距雙市場最近一次核驗已超過四個日曆日，請查看發布流程及休市資訊。</p>}
      <section className="panel overflow-hidden">
        <div className="flex flex-wrap gap-3 p-4">
          <input aria-label="搜尋股票" placeholder="股票代號或名稱" value={query} onChange={event => {setQuery(event.target.value); setPage(1);}}/>
          <select aria-label="市場" value={market} onChange={event => {setMarket(event.target.value); setPage(1);}}>
            <option value="">所有市場</option><option value="TWSE">上市</option><option value="TPEx">上櫃</option>
          </select>
          <select aria-label="排序" value={order} onChange={event => {setOrder(event.target.value); setPage(1);}}>
            <option value="rank">股票池排名</option><option value="maintenance">維持率由低至高</option>
          </select>
          <button onClick={() => csvDownload('margin-stocks.csv', [
            ['代號', '名稱', '行情日期', '最後可估算日', '當日資料日期', '收盤', '估算成本', '維持率%', '融資餘額', '品質', '快照', '模型'],
            ...rows.map(stock => [stock.code, stock.name, stock.latest_quote_date, stock.latest_estimate_date,
              stock.latest?.date, stock.latest?.close, stock.latest?.cost, stock.latest?.maintenance,
              stock.latest?.balance, stock.latest?.quality, data.snapshot_id, data.model]),
          ])}>匯出篩選結果</button>
        </div>
        <div className="overflow-x-auto"><table className="w-full whitespace-nowrap">
          <thead><tr>{['股票', '市場', '行情日', '收盤', '推估成本', '維持率', '最後可估算日', '品質'].map(title => <th key={title}>{title}</th>)}</tr></thead>
          <tbody>{visible.map(stock => <tr key={stock.security_id}>
            <td><a className="text-teal-300 underline-offset-4 hover:underline" href={stockLink(stock, data.snapshot_id)}>{stock.code} {stock.name}</a></td>
            <td>{stock.market === 'TWSE' ? '上市' : stock.market === 'TPEx' ? '上櫃' : '待查'}</td>
            <td>{stock.latest_quote_date || '—'}</td><td>{fmt(stock.latest?.close)}</td>
            <td>{fmt(stock.latest?.cost)}</td><td>{stock.latest?.maintenance == null ? '—' : fmt(stock.latest.maintenance) + '%'}</td>
            <td>{stock.latest_estimate_date || '—'}</td><td className="text-xs text-amber-200">{labels[stock.latest?.quality || ''] || '待查'}</td>
          </tr>)}</tbody>
        </table></div>
        {!visible.length && <p className="p-6 text-slate-400">沒有符合條件的股票。</p>}
        <div className="flex items-center justify-end gap-3 p-4 text-sm">
          <button disabled={page <= 1} onClick={() => setPage(page - 1)}>上一頁</button>
          <span>{rows.length} 檔 · {page} / {lastPage}</span>
          <button disabled={page >= lastPage} onClick={() => setPage(page + 1)}>下一頁</button>
        </div>
      </section>
    </>}
  </main>;
}
