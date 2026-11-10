"use client";

import {useEffect, useMemo, useRef, useState} from 'react';
import {ColorType, createChart, LineSeries, type Time} from 'lightweight-charts';
import {base, csvDownload, fmt, getJSON} from './siteData';

type MarketDay = [date: string, estimate: number | null, reference: number | null,
  included: number | null, positive: number | null, dbCoverage: number | null,
  marketCoverage: number | null, seedWeight: number | null, missingMargin: number | null,
  collateralNtd: number | null, debtNtd: number | null];
type StockDay = {date: string; candidatePct: number; publishedPct: number | null};
type MarketData = {
  version: 2;
  rows: MarketDay[];
  rebuild: {model: string; dataVersion: string; stocks: number; modeledStocks: number; stockDays: number;
    modeledStockDays: number; firstDate: string; lastDate: string; dailyRows: number};
  material: {seedDate: string; rows: StockDay[]};
  stockAudit: {checks: number; stocks: number; maePp: number; maxPp: number};
  split: {date: string; publishedPct: number; candidatePct: number; adjustedPct: number};
};
type ChartDay = {date: string; estimate: number | null; reference: number | null};

const source = 'https://www.wantgoo.com/stock/margin-trading/exclude-etf/taiex';
const comparisonDate = '2026-07-28';
const chartRanges = {recent: '最近 6 個月', july: '7/28 前後', all: '完整歷史'} as const;
const estimateColor = '#55e1ca';
const referenceColor = '#91b8ff';
const percentage = (value: number | null | undefined, digits = 3) => value == null ? '—' : fmt(value, digits) + '%';
const coverage = (value: number | null) => value == null ? '—' : percentage(value * 100, 2);

function chartDate(time: Time): string {
  if (typeof time === 'string') return time;
  if (typeof time === 'number') return new Date(time * 1000).toISOString().slice(0, 10);
  return [time.year, String(time.month).padStart(2, '0'), String(time.day).padStart(2, '0')].join('-');
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
      timeScale: {borderColor: '#334155', minBarSpacing: .05},
      localization: {priceFormatter: (price: number) => price.toFixed(1) + '%'},
    });
    for (const [key, color, width] of [['reference', referenceColor, 1], ['estimate', estimateColor, 2]] as const) {
      let run: {time: string; value: number}[] = [];
      const flush = () => {
        if (!run.length) return;
        const series = chart.addSeries(LineSeries, {color, lineWidth: width, priceLineVisible: false,
          lastValueVisible: false, pointMarkersVisible: false, crosshairMarkerVisible: false});
        series.setData(run);
        run = [];
      };
      for (const row of rows) {
        const value = row[key];
        if (value == null) flush();
        else run.push({time: row.date, value});
      }
      flush();
    }
    if (onDate) {
      chart.subscribeCrosshairMove(({time}) => { if (time !== undefined) onDate(chartDate(time)); });
      chart.subscribeClick(({time}) => { if (time !== undefined) onDate(chartDate(time)); });
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [rows, height, onDate]);
  return <div ref={container} role="img" aria-label={label}/>;
}

export default function MarketMaintenance() {
  const [data, setData] = useState<MarketData | null>(null);
  const [error, setError] = useState('');
  const [range, setRange] = useState<keyof typeof chartRanges>('recent');
  const [selectedDate, setSelectedDate] = useState('');
  useEffect(() => {
    let active = true;
    getJSON<MarketData>(base + '/data/wantgoo-market.json').then(payload => {
      if (payload.version !== 2 || !payload.rows.length) throw new Error('大盤重算快照格式不符。');
      if (active) setData(payload);
    }).catch((err: Error) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, []);

  const marketRows = useMemo(() => {
    if (!data) return [];
    if (range === 'july') return data.rows.filter(([day]) => day >= '2026-07-01' && day <= '2026-08-14');
    if (range === 'all') return data.rows;
    const cutoff = new Date(data.rows.at(-1)![0] + 'T00:00:00Z');
    cutoff.setUTCMonth(cutoff.getUTCMonth() - 6);
    return data.rows.filter(([day]) => day >= cutoff.toISOString().slice(0, 10));
  }, [data, range]);
  const marketChart = useMemo(() => marketRows.map(([date, estimate, reference]) => ({date, estimate, reference})), [marketRows]);
  const stockChart = useMemo(() => data?.material.rows.map(row => ({
    date: row.date, estimate: row.candidatePct, reference: null,
  })) ?? [], [data]);

  const selected = marketRows.find(([day]) => day === selectedDate) ?? marketRows.at(-1);
  const latest = data?.rows.findLast(row => row[1] != null);
  const errorPp = selected?.[1] != null && selected[2] != null ? selected[1] - selected[2] : null;

  return <main className="mx-auto max-w-7xl px-4 py-8 sm:py-10">
    <a className="text-sm text-teal-300 hover:underline" href={base + '/'}>← 台股融資維持率首頁</a>
    <p className="mt-7 text-xs font-semibold tracking-widest text-teal-300">TAIWAN MARGIN MONITOR</p>
    <h1 className="mt-2 text-3xl font-semibold text-white">上市大盤融資維持率</h1>
    <p className="mt-2 text-sm leading-6 text-slate-400">排除 ETF · 資料庫逐股成本遞推與玩股網原始值</p>
    {error && <p role="alert" className="mt-5 text-rose-300">{error}</p>}
    {!data && !error && <p role="status" className="mt-5 text-slate-400">載入大盤重算資料…</p>}
    {data && latest && <>
      <div className="panel my-6 grid grid-cols-2 gap-5 p-5 sm:grid-cols-4">
        <div><p className="text-xs text-slate-400">最新資料庫重算值</p><p className="mt-1 text-xl tabular-nums text-white">{percentage(latest[1])}</p><p className="text-xs text-slate-400">{latest[0]}</p></div>
        <div><p className="text-xs text-slate-400">上市非 ETF 日序列</p><p className="mt-1 text-xl tabular-nums text-white">{fmt(data.rebuild.dailyRows, 0)} 日</p><p className="text-xs text-slate-400">自 {data.rebuild.firstDate}</p></div>
        <div><p className="text-xs text-slate-400">全庫處理</p><p className="mt-1 text-xl tabular-nums text-white">{data.rebuild.stocks} 檔</p><p className="text-xs text-slate-400">{fmt(data.rebuild.stockDays, 0)} 筆證券日</p></div>
        <div><p className="text-xs text-slate-400">產生維持率</p><p className="mt-1 text-xl tabular-nums text-white">{fmt(data.rebuild.modeledStockDays, 0)} 筆</p><p className="text-xs text-slate-400">{data.rebuild.modeledStocks} 檔含可計算日期</p></div>
      </div>

      <section className="panel overflow-hidden" aria-labelledby="market-history">
        <div className="space-y-4 p-4 sm:p-5">
          <h2 id="market-history" className="text-lg font-semibold text-white">每日重算曲線</h2>
          <div className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-slate-300">
            <span><span aria-hidden className="mr-2 inline-block h-0.5 w-5 align-middle bg-[#55e1ca]"/>資料庫重算</span>
            <span><span aria-hidden className="mr-2 inline-block h-0.5 w-5 align-middle bg-[#91b8ff]"/>玩股網原始值</span>
          </div>
          <p className="text-xs leading-5 text-slate-400">重算曲線涵蓋資料庫內的上市非 ETF 股票。各日納入成分隨歷史資料覆蓋而變，早期上市股數較少。</p>
          <div className="flex flex-wrap gap-2" aria-label="圖表區間">
            {Object.entries(chartRanges).map(([value, label]) => <button key={value} type="button"
              aria-pressed={range === value} className={range === value ? 'border-teal-400 bg-teal-400/10 text-teal-100' : ''}
              onClick={() => { setRange(value as keyof typeof chartRanges); if (value === 'july') setSelectedDate(comparisonDate); }}>{label}</button>)}
          </div>
        </div>
        <ComparisonChart rows={marketChart} label="資料庫每日重算曲線及玩股網原始維持率曲線" height={330} onDate={setSelectedDate}/>
        {selected && <div className="border-t border-slate-800 p-4 sm:p-5">
          <label className="flex flex-wrap items-center gap-3 text-sm text-slate-300">查看日期
            <select value={selected[0]} onChange={event => setSelectedDate(event.target.value)}>
              {[...marketRows].reverse().map(([day]) => <option key={day}>{day}</option>)}
            </select>
          </label>
          <dl className="mt-4 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
            <div><dt className="text-slate-400">資料庫重算值</dt><dd className="mt-1 tabular-nums text-teal-200">{percentage(selected[1])}</dd></div>
            <div><dt className="text-slate-400">玩股網原始值</dt><dd className="mt-1 tabular-nums text-blue-200">{percentage(selected[2])}</dd></div>
            <div><dt className="text-slate-400">重算減原始值</dt><dd className="mt-1 tabular-nums">{errorPp == null ? '—' : (errorPp > 0 ? '+' : '') + fmt(errorPp, 3) + ' 個百分點'}</dd></div>
            <div><dt className="text-slate-400">納入／庫內有融資餘額</dt><dd className="mt-1 tabular-nums">{selected[3] == null ? '—' : selected[3] + '／' + selected[4] + ' 檔'}</dd></div>
          </dl>
          <p className="mt-4 text-xs leading-6 text-slate-400">
            庫內已定價市值涵蓋 {coverage(selected[5])} · 相對交易所已定價全市場 {coverage(selected[6])} · 估算起點成本權重 {coverage(selected[7])}
          </p>
        </div>}
      </section>

      <details className="panel mt-6 overflow-hidden">
        <summary className="cursor-pointer p-4 font-semibold text-white sm:p-5">算法、資料範圍及 4763 驗證</summary>
        <div className="space-y-3 px-4 pb-4 text-sm leading-7 text-slate-300 sm:px-5">
          <p>新增融資買進按當日成交均價入帳，其餘部位沿用前一日平均成本。每日以合計融資市值除以 60% × 合計成本，7/28 前後使用同一個遞推規則。</p>
          <p>若首日已有舊餘額而成本未入庫，以該日成交均價估算起點。缺融資買進、成交均價或收盤價的個股日保留狀態。公司行動按已核對的個別條款處理，其他事件沿用名目成本並標記。大盤原始值僅用於對照。</p>
          <p>沛爾生醫（6949）於 2026/9/7 一拆二十。重算依證交所換股率及玩股網個股核對結果換算前期成本。材料*-KY（4763）在 2025/6/30 的觀測則符合延續原名目成本。</p>
          <p>材料*-KY 以 {data.material.seedDate} 設定一次起始成本後連算 {stockChart.length} 日。先前 {data.stockAudit.stocks} 檔的 {data.stockAudit.checks} 筆連算核對平均絕對誤差為 {fmt(data.stockAudit.maePp, 5)} 個百分點。</p>
          <p className="text-xs text-slate-400">模型 {data.rebuild.model} · 資料版本 {data.rebuild.dataVersion.slice(0, 12)}</p>
        </div>
        <ComparisonChart rows={stockChart} label="材料*-KY 單次設定起始成本的連算曲線" height={220}/>
      </details>
      <div className="mt-5 flex flex-wrap items-center gap-3 text-sm text-teal-300">
        <button type="button" onClick={() => csvDownload('wantgoo-rebuilt-market-maintenance.csv', [
          ['日期', '資料庫重算%', '玩股網原始值%', '納入股數', '庫內正餘額股數', '庫內已定價市值涵蓋率%', '全市場已定價市值涵蓋率%',
            '估算起點成本權重%', '缺融資股數', '納入融資市值（元）', '估算融資本金（元）'],
          ...data.rows.map(([day, estimate, reference, included, positive, db, market, seed, missing, collateral, debt]) =>
            [day, estimate, reference, included, positive, db == null ? null : db * 100, market == null ? null : market * 100,
              seed == null ? null : seed * 100, missing, collateral, debt]),
        ])}>下載重算日資料 CSV</button>
        <a href={source} className="px-2 hover:underline">玩股網資料來源 ↗</a>
      </div>
    </>}
  </main>;
}
