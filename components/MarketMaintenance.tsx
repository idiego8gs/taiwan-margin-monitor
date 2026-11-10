"use client";

import {useEffect, useMemo, useRef, useState} from 'react';
import {ColorType, createChart, LineSeries, type Time} from 'lightweight-charts';
import {base, csvDownload, fmt, getJSON} from './siteData';

type Sample = {pct: number; errorPp: number; coverage: number; included: number; total: number};
type MarketDay = {date: string; publishedPct: number; balance100m: number; sample: Sample | null};
type StockDay = {date: string; candidatePct: number; publishedPct: number | null};
type MarketData = {
  version: 1;
  minCoverage: number;
  rows: MarketDay[];
  material: {seedDate: string; rows: StockDay[]};
  stockAudit: {checks: number; stocks: number; maePp: number; maxPp: number};
  split: {date: string; publishedPct: number; candidatePct: number; adjustedPct: number};
};
type ChartDay = {date: string; line: number; point: number | null};

const source = 'https://www.wantgoo.com/stock/margin-trading/exclude-etf/taiex';
const comparisonDate = '2026-07-28';
const chartRanges = {recent: '最近 6 個月', july: '7/28 前後', all: '全部日期'} as const;
const percentage = (value: number | null | undefined, digits = 3) => value == null ? '—' : `${fmt(value, digits)}%`;
const difference = (value: number | undefined) => value == null ? '—' : `${value > 0 ? '+' : ''}${fmt(value, 3)} 個百分點`;

function chartDate(time: Time): string {
  if (typeof time === 'string') return time;
  if (typeof time === 'number') return new Date(time * 1000).toISOString().slice(0, 10);
  return `${time.year}-${String(time.month).padStart(2, '0')}-${String(time.day).padStart(2, '0')}`;
}

function ComparisonChart({rows, label, height, onDate}: {
  rows: ChartDay[]; label: string; height: number; onDate?: (date: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current || !rows.length) return;
    const chart = createChart(container.current, {
      autoSize: true, height,
      layout: {background: {type: ColorType.Solid, color: '#101925'}, textColor: '#b5c6d8'},
      grid: {vertLines: {color: '#1b2938'}, horzLines: {color: '#253244'}},
      rightPriceScale: {minimumWidth: 64, borderColor: '#334155'},
      timeScale: {borderColor: '#334155'},
      localization: {priceFormatter: (price: number) => `${price.toFixed(1)}%`},
    });
    const line = chart.addSeries(LineSeries, {color: '#55e1ca', lineWidth: 2, priceLineVisible: false, lastValueVisible: false});
    line.setData(rows.map(row => ({time: row.date, value: row.line})));
    const points = chart.addSeries(LineSeries, {
      color: '#f4bf62', lineVisible: false, pointMarkersVisible: true, pointMarkersRadius: 3,
      crosshairMarkerVisible: false, priceLineVisible: false, lastValueVisible: false,
    });
    points.setData(rows.flatMap(row => row.point == null ? [] : [{time: row.date, value: row.point}]));
    if (onDate) {
      chart.subscribeCrosshairMove(({time}) => { if (time !== undefined) onDate(chartDate(time)); });
      chart.subscribeClick(({time}) => { if (time !== undefined) onDate(chartDate(time)); });
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [rows, height, onDate]);
  return <div ref={container} role="img" aria-label={label}/>;
}

function Key({line, point}: {line: string; point: string}) {
  return <div className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-slate-300">
    <span><span aria-hidden className="mr-2 inline-block h-0.5 w-5 align-middle bg-[#55e1ca]"/>{line}</span>
    <span><span aria-hidden className="mr-2 inline-block h-2 w-2 rounded-full bg-[#f4bf62]"/>{point}</span>
  </div>;
}

export default function MarketMaintenance() {
  const [data, setData] = useState<MarketData | null>(null);
  const [error, setError] = useState('');
  const [range, setRange] = useState<keyof typeof chartRanges>('recent');
  const [selectedDate, setSelectedDate] = useState('');
  useEffect(() => {
    let active = true;
    getJSON<MarketData>(`${base}/data/wantgoo-market.json`).then(payload => {
      if (payload.version !== 1 || !payload.rows.length) throw new Error('大盤研究快照格式不符。');
      if (active) setData(payload);
    }).catch((err: Error) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, []);

  const marketRows = useMemo(() => {
    if (!data) return [];
    if (range === 'july') return data.rows.filter(row => row.date >= '2026-07-01' && row.date <= '2026-08-14');
    if (range === 'all') return data.rows;
    const cutoff = new Date(data.rows.at(-1)!.date + 'T00:00:00Z');
    cutoff.setUTCMonth(cutoff.getUTCMonth() - 6);
    return data.rows.filter(row => row.date >= cutoff.toISOString().slice(0, 10));
  }, [data, range]);
  const marketChart = useMemo(() => marketRows.map(row => ({
    date: row.date, line: row.publishedPct,
    point: row.sample && row.sample.coverage >= (data?.minCoverage ?? 1) ? row.sample.pct : null,
  })), [marketRows, data]);
  const stockChart = useMemo(() => data?.material.rows.map(row => ({
    date: row.date, line: row.candidatePct, point: row.publishedPct,
  })) ?? [], [data]);

  const selected = marketRows.find(row => row.date === selectedDate) ?? marketRows.at(-1);
  const sample = selected?.sample;
  const latest = data?.rows.at(-1);
  const audits = data?.rows.flatMap(row => row.sample && row.sample.coverage >= data.minCoverage ? [row.sample] : []) ?? [];
  const errors = audits.map(row => Math.abs(row.errorPp));
  const coverages = audits.map(row => row.coverage * 100);
  const materialErrors = data?.material.rows.flatMap(row => row.publishedPct == null ? [] : [Math.abs(row.candidatePct - row.publishedPct)]) ?? [];
  const mae = (values: number[]) => values.length ? values.reduce((total, value) => total + value, 0) / values.length : null;

  return <main className="mx-auto max-w-7xl px-4 py-8 sm:py-10">
    <a className="text-sm text-teal-300 hover:underline" href={`${base}/`}>← 台股融資維持率首頁</a>
    <p className="mt-7 text-xs font-semibold tracking-widest text-teal-300">TAIWAN MARGIN MONITOR</p>
    <h1 className="mt-2 text-3xl font-semibold text-white">上市大盤融資維持率</h1>
    <p className="mt-2 text-sm leading-6 text-slate-400">排除 ETF · 玩股網歷史快照與統一成本公式核對</p>
    {error && <p role="alert" className="mt-5 text-rose-300">{error}</p>}
    {!data && !error && <p role="status" className="mt-5 text-slate-400">載入大盤研究資料…</p>}
    {data && latest && <>
      <div className="panel my-6 grid grid-cols-2 gap-5 p-5 sm:grid-cols-4">
        <div><p className="text-xs text-slate-400">最新收錄原始值</p><p className="mt-1 text-xl tabular-nums text-white">{percentage(latest.publishedPct)}</p><p className="text-xs text-slate-400">{latest.date}</p></div>
        <div><p className="text-xs text-slate-400">原始觀測</p><p className="mt-1 text-xl tabular-nums text-white">{data.rows.length} 日</p><p className="text-xs text-slate-400">自 {data.rows[0].date}</p></div>
        <div><p className="text-xs text-slate-400">大盤抽樣核對</p><p className="mt-1 text-xl tabular-nums text-white">{audits.length} 日</p><p className="text-xs text-slate-400">平均絕對誤差 {fmt(mae(errors))} 個百分點</p></div>
        <div><p className="text-xs text-slate-400">個股連算驗證</p><p className="mt-1 text-xl tabular-nums text-white">{data.stockAudit.checks} 筆</p><p className="text-xs text-slate-400">{data.stockAudit.stocks} 檔各設定一次起點</p></div>
      </div>

      <section className="panel overflow-hidden" aria-labelledby="market-history">
        <div className="space-y-4 p-4 sm:p-5">
          <h2 id="market-history" className="text-lg font-semibold text-white">大盤日線與抽樣核對</h2>
          <Key line="玩股網原始值" point="部分成分股加總"/>
          <div className="flex flex-wrap gap-2" aria-label="圖表區間">
            {Object.entries(chartRanges).map(([value, label]) => <button key={value} type="button"
              aria-pressed={range === value} className={range === value ? 'border-teal-400 bg-teal-400/10 text-teal-100' : ''}
              onClick={() => { setRange(value as keyof typeof chartRanges); if (value === 'july') setSelectedDate(comparisonDate); }}>{label}</button>)}
          </div>
        </div>
        <ComparisonChart rows={marketChart} label="玩股網原始維持率曲線及部分成分股加總點" height={330} onDate={setSelectedDate}/>
        {selected && <div className="border-t border-slate-800 p-4 sm:p-5">
          <label className="flex flex-wrap items-center gap-3 text-sm text-slate-300">查看日期
            <select value={selected.date} onChange={event => setSelectedDate(event.target.value)}>
              {[...marketRows].reverse().map(row => <option key={row.date}>{row.date}</option>)}
            </select>
          </label>
          <dl className="mt-4 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
            <div><dt className="text-slate-400">玩股網原始值</dt><dd className="mt-1 tabular-nums text-teal-200">{percentage(selected.publishedPct)}</dd></div>
            <div><dt className="text-slate-400">部分成分股加總</dt><dd className="mt-1 tabular-nums text-amber-200">{percentage(sample?.pct)}</dd></div>
            <div><dt className="text-slate-400">加總與原始值差異</dt><dd className="mt-1 tabular-nums">{difference(sample?.errorPp)}</dd></div>
            <div><dt className="text-slate-400">已定價市值涵蓋率</dt><dd className="mt-1 tabular-nums">{percentage(sample && sample.coverage * 100, 2)}{sample && <span className="block text-xs text-slate-400">{sample.included}／{sample.total} 檔</span>}</dd></div>
          </dl>
          {sample && sample.coverage < data.minCoverage && <p className="mt-3 text-xs text-amber-200">此日涵蓋率低於 {percentage(data.minCoverage * 100, 0)}，保留數值供查看，圖表與摘要排除此日。</p>}
          {!sample && <p className="mt-3 text-xs text-slate-400">此日只有玩股網原始觀測。</p>}
        </div>}
      </section>

      <section className="panel mt-6 overflow-hidden" aria-labelledby="stock-continuity">
        <div className="space-y-3 p-4 sm:p-5">
          <h2 id="stock-continuity" className="text-lg font-semibold text-white">材料*-KY（4763）跨 7/28 連算</h2>
          <p className="max-w-3xl text-sm leading-6 text-slate-400">以 {data.material.seedDate} 的個股比率設定一次起始成本，連算 {stockChart.length} 個交易日。{materialErrors.length} 個可核對日期的最大誤差為 {fmt(materialErrors.length ? Math.max(...materialErrors) : null, 4)} 個百分點。</p>
          <Key line="統一公式連算" point="玩股網個股觀測"/>
        </div>
        <ComparisonChart rows={stockChart} label="材料*-KY 統一公式連算曲線與個股觀測點" height={220}/>
      </section>

      <details className="panel mt-6 p-4 text-sm sm:p-5">
        <summary className="cursor-pointer font-semibold text-white">算法與資料涵蓋</summary>
        <div className="mt-3 space-y-3 leading-7 text-slate-300">
          <p>每檔股票的新增融資買進按當日成交均價計入成本，其餘融資部位沿用前一日平均成本。大盤維持率為 100 × 融資市值合計 ÷（60% × 融資成本合計），分子與分母均採上市非 ETF 股票。7/28 前後使用同一條公式。</p>
          <p>大盤抽樣使用已公布個股比率反推成本。圖中日期的已定價融資市值涵蓋率為 {percentage(coverages.length ? Math.min(...coverages) : null, 2)} 至 {percentage(coverages.length ? Math.max(...coverages) : null, 2)}。全市場逐日連算仍需補齊起始成本、每日買進及成交資料。</p>
          <p>{data.stockAudit.stocks} 檔連算的平均絕對誤差為 {fmt(data.stockAudit.maePp, 5)} 個百分點，最大為 {fmt(data.stockAudit.maxPp, 5)} 個百分點。</p>
          <p>材料*-KY 在 {data.split.date} 分割復牌。沿用前期名目成本得到 {percentage(data.split.candidatePct, 6)}，接近當日公布的 {percentage(data.split.publishedPct)}。若先調整分割成本，試算值為 {percentage(data.split.adjustedPct, 6)}。</p>
          <p className="text-xs text-slate-400">研究快照涵蓋至 {latest.date}。原始值與成交均價來自玩股網，融資資料另以證交所及既有 FinMind 封存核對。</p>
        </div>
      </details>
      <div className="mt-5 flex flex-wrap items-center gap-3 text-sm text-teal-300">
        <button type="button" onClick={() => csvDownload('wantgoo-market-maintenance.csv', [
          ['日期', '玩股網維持率%', '玩股網融資餘額（億元）', '抽樣加總%', '誤差（百分點）', '涵蓋率%', '納入股數', '成分股數', '納入核對摘要'],
          ...data.rows.map(row => [row.date, row.publishedPct, row.balance100m, row.sample?.pct, row.sample?.errorPp,
            row.sample ? row.sample.coverage * 100 : null, row.sample?.included, row.sample?.total,
            row.sample ? (row.sample.coverage >= data.minCoverage ? '是' : '否') : null]),
        ])}>下載大盤 CSV</button>
        <button type="button" onClick={() => csvDownload('4763-continuity.csv', [
          ['日期', '起始日', '連算維持率%', '玩股網維持率%', '誤差（百分點）'],
          ...data.material.rows.map(row => [row.date, data.material.seedDate, row.candidatePct, row.publishedPct,
            row.publishedPct == null ? null : row.candidatePct - row.publishedPct]),
        ])}>下載 4763 連算 CSV</button>
        <a href={source} className="px-2 hover:underline">玩股網資料來源 ↗</a>
      </div>
    </>}
  </main>;
}
