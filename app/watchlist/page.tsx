"use client";

import {useEffect, useState} from 'react';

const api = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
type Watch = {code: string; maintenance_threshold: number; distance_threshold: number};

export default function Watchlist() {
  const [items, setItems] = useState<Watch[]>([]);
  const [code, setCode] = useState('');
  const [threshold, setThreshold] = useState('140');
  const [distance, setDistance] = useState('5');
  const [error, setError] = useState('');
  async function refresh() {
    const response = await fetch(api + '/api/watchlist');
    if (!response.ok) throw new Error();
    setItems(await response.json());
  }
  useEffect(() => {refresh().catch(() => setError('無法連線自選股 API'));}, []);
  async function change(method: 'POST' | 'DELETE', stock: string) {
    setError('');
    try {
      const response = method === 'POST' ? await fetch(api + '/api/watchlist', {
        method, headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({code: stock, maintenance_threshold: Number(threshold), distance_threshold: Number(distance)}),
      }) : await fetch(api + '/api/watchlist/' + encodeURIComponent(stock), {method});
      if (!response.ok) throw new Error();
      await refresh();
      if (method === 'POST') setCode('');
    } catch {setError('無法儲存變更，請檢查股票代號、門檻和 API 連線。');}
  }
  return <main className="mx-auto max-w-4xl px-4 py-8">
    <a href="/" className="text-teal-300">← 股票池</a>
    <h1 className="my-5 text-2xl font-semibold">自選股</h1>
    <div className="panel p-5">
      <p className="mb-4 text-sm text-slate-400">儲存自選股與警戒設定。</p>
      <div className="flex flex-wrap gap-3">
        <input aria-label="股票代號" placeholder="股票代號" value={code} onChange={event => setCode(event.target.value)}/>
        <input aria-label="維持率警戒" type="number" value={threshold} onChange={event => setThreshold(event.target.value)}/>
        <input aria-label="距離警戒" type="number" value={distance} onChange={event => setDistance(event.target.value)}/>
        <button onClick={() => change('POST', code)}>加入 / 更新</button>
      </div>
      {error && <p role="alert" className="mt-4 text-rose-300">{error}</p>}
      {items.map(item => <div key={item.code} className="mt-4 flex items-center justify-between gap-3 border-t border-slate-700 pt-3">
        <span>{item.code} · 維持率 &lt; {item.maintenance_threshold}% · 距 130% &lt; {item.distance_threshold}%</span>
        <button onClick={() => change('DELETE', item.code)}>移除</button>
      </div>)}
    </div>
  </main>;
}
