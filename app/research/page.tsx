"use client";
import {useEffect,useState} from 'react';
const API=process.env.NEXT_PUBLIC_API_URL||'http://localhost:8000';
type Run={id:string;status:string;data_version:string;heartbeat_at:string|null;progress:{stage?:string;completed?:number;total?:number};last_error:string|null};
type Result={quality:string;threshold:number;holding_days:number;trend:string;candidate:boolean;by_q:number|null;bh_q:number|null;
 summary:{signals:number;matched:number;signal_dates:number;pending?:number;mature_unpaired_pct?:number|null;
  mean_observed_signal_net_pct?:number|null;mean_signal_net_pct:number|null;mean_excess_pp:number|null;attrition:Record<string,number>;
  months_descriptive?:Record<string,{matched:number;share_pct:number;without_month_net_pct:number|null;without_month_excess_pp:number|null}>};
 inference:{status:string;p_value:number|null;ci95_pp:number[]|null}};
const fmt=(n:number|null|undefined,d=2)=>n==null?'—':Number(n).toFixed(d);
const show=(n:number|null|undefined,unit:string)=>n==null?'—':`${fmt(n)}${unit}`;
const reasons:Record<string,string>={control_no_eligible_control:'找不到相近對照',control_corporate_action_in_window:'對照期有公司行動',signal_corporate_action_in_window:'訊號期有公司行動',control_outside_frozen_calendar:'對照期超出快照',signal_outside_frozen_calendar:'訊號期超出快照',control_missing_price_or_suspension:'對照缺價或停牌',signal_missing_price_or_suspension:'訊號缺價或停牌',control_action_coverage_unverified:'對照公司行動待查',signal_action_coverage_unverified:'訊號公司行動待查'};
const groups:Record<string,string>={all:'不分趨勢',bull_bull:'個股多頭／大盤多頭',bull_nonbull:'個股多頭／大盤非多頭',nonbull_bull:'個股非多頭／大盤多頭',nonbull_nonbull:'個股非多頭／大盤非多頭'};
const columns=['持有日','訊號數','未到出場日','有效配對','成熟未配對率','有效配對日數',
 '全部可觀察單筆淨報酬','配對子樣本單筆淨報酬','配對超額','95% 區間','p 值','BY q 值','逐月敏感度'];
export default function Research(){
 const [run,setRun]=useState<Run|null>(null),[results,setResults]=useState<Result[]>([]),[quality,setQuality]=useState('sensitivity_5pct'),[threshold,setThreshold]=useState(130),[trend,setTrend]=useState('all'),[error,setError]=useState('');
 useEffect(()=>{let active=true;const load=async()=>{try{
  const a=await fetch(`${API}/api/research/runs`,{cache:'no-store'});if(!a.ok)throw Error();const list=await a.json();const latest=list.items[0]||null;
  if(!active)return;setRun(latest);if(!latest||latest.status==='invalidated'){setResults([]);return}
  const q=new URLSearchParams({quality,threshold:String(threshold),trend,limit:'100'});const b=await fetch(`${API}/api/research/runs/${latest.id}/results?${q}`,{cache:'no-store'});if(!b.ok)throw Error();const data=await b.json();if(active){setResults(data.items.sort((x:Result,y:Result)=>x.holding_days-y.holding_days));setError('')}
 }catch{if(active)setError('無法載入研究資料；請確認本機 API 已啟動。')}};load();const timer=setInterval(load,15000);return()=>{active=false;clearInterval(timer)}},[quality,threshold,trend]);
 return <main className="mx-auto max-w-7xl px-5 py-8"><div className="flex gap-6 text-sm"><a className="text-teal-300" href="/">← 首頁</a><a className="text-teal-300" href="/progress">資料進度</a></div><p className="mt-10 text-xs tracking-widest text-teal-400">LOCAL RESEARCH / EXPLORATORY</p><h1 className="mt-3 text-3xl font-semibold text-white">融資維持率短線回測</h1><p className="mt-3 text-sm leading-7 text-slate-400">新版在各趨勢組獨立決定進場。對照股票須同日、同市場、同趨勢，再按事前跌幅、波動與成交額匹配。</p>
 <div className="panel mt-6 p-5 text-sm leading-7 text-amber-100">這是探索性研究，不是投資建議或實盤成交結果。歷史公布時間、當時股票池及公司行動尚未完全核實；已查看過的歷史資料不是乾淨的樣本外測試。初始成本敏感度低於 5% 不代表實際成本誤差低於 5%。130% 是個股模型參考線，實際追繳依投資人整戶維持率。</div>
 {error&&<p role="alert" className="mt-4 text-red-300">{error}</p>}
 <div className="panel mt-5 p-5 text-sm"><div className="text-white">{run?`狀態：${run.status==='invalidated'?'資料待重新核對':run.status} · 完成 ${run.progress?.completed||0} / ${run.progress?.total||800} 組`:'尚未建立回測'}</div>{run?.status==='invalidated'&&<p className="mt-2 text-amber-300">已發現面額變更事件尚未納入舊版模型，暫停顯示舊結果，正在重算。</p>}{run&&<p className="mt-2 break-all text-xs text-slate-500">資料版本 {run.data_version}<br/>研究版本 {run.id}<br/>最後心跳 {run.heartbeat_at||'—'}{run.last_error&&` · 錯誤：${run.last_error}`}</p>}</div>
 <div className="mt-5 flex flex-wrap gap-3"><label className="text-sm text-slate-400">品質 <select value={quality} onChange={e=>setQuality(e.target.value)}><option value="sensitivity_5pct">探索：初始成本敏感度 ≤5%</option><option value="strict">成本暖機：初始成本影響已消除</option></select></label><label className="text-sm text-slate-400">跌破 <select value={threshold} onChange={e=>setThreshold(Number(e.target.value))}>{[130,125,120,110].map(n=><option key={n} value={n}>{n}%</option>)}</select></label><label className="text-sm text-slate-400">趨勢 <select value={trend} onChange={e=>setTrend(e.target.value)}>{Object.entries(groups).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label></div>
 <section className="panel mt-4 overflow-x-auto">
 <table className="w-full text-left text-sm">
 <thead><tr>{columns.map(x=><th className="whitespace-nowrap p-3" key={x}>{x}</th>)}</tr></thead>
 <tbody>{results.map(r=>{
   const s=r.summary;
   return <tr className="border-t border-slate-800" key={r.holding_days}>
    <td className="p-3">{r.holding_days}</td><td className="p-3">{s.signals}</td>
    <td className="p-3">{s.pending??'—'}</td><td className="p-3">{s.matched}</td>
    <td className="p-3"><details><summary className="cursor-pointer text-teal-300">{show(s.mature_unpaired_pct,'%')}</summary>
     <div className="min-w-48 text-xs leading-6 text-slate-400">{Object.entries(s.attrition).map(([k,v])=><div key={k}>{reasons[k]||k}：{v}</div>)}</div>
    </details></td><td className="p-3">{s.signal_dates}</td>
    <td className="p-3">{show(s.mean_observed_signal_net_pct,'%')}</td>
    <td className="p-3">{show(s.mean_signal_net_pct,'%')}</td><td className="p-3">{show(s.mean_excess_pp,' pp')}</td>
    <td className="whitespace-nowrap p-3">{r.inference.ci95_pp?`${fmt(r.inference.ci95_pp[0])}～${fmt(r.inference.ci95_pp[1])} pp`:'樣本不足或未完成'}</td>
    <td className="p-3">{fmt(r.inference.p_value,4)}</td><td className="p-3">{fmt(r.by_q,4)}</td>
    <td className="p-3">{s.months_descriptive&&Object.keys(s.months_descriptive).length>0?
      <details><summary className="cursor-pointer text-teal-300">逐一移除月份</summary>
      <div className="min-w-64 text-xs leading-6 text-slate-400">{Object.entries(s.months_descriptive).map(([month,m])=>
       <div key={month}>{month} · {m.matched} 筆（{fmt(m.share_pct,1)}%）· 移除後淨報酬 {show(m.without_month_net_pct,'%')}、超額 {show(m.without_month_excess_pp,' pp')}</div>)}</div>
      </details>:'—'}</td>
   </tr>
 })}</tbody></table>{!results.length&&<p className="p-6 text-sm text-slate-500">此條件尚未完成；畫面每 15 秒更新。</p>}
 {results.length>0&&results[0].summary.pending===undefined&&<p className="p-4 text-amber-200">舊版研究使用交易後趨勢分組及股利調整成本，僅供比較。</p>}</section>
 <p className="mt-5 text-xs leading-6 text-slate-500">新版採收盤價、融資成數假設 60%，現金股利另列，不自動減少估計本金。模型尚未校準。假設訊號後次日開盤買入、第 h 個交易日收盤賣出；手續費每邊 0.1425%、賣出稅 0.3%、滑價每邊 0.1%。訊號日不足 30 天不做顯著性判斷；訊號日數不等於獨立樣本。q 值須待 800 組全部完成後才顯示。排除原因可能重疊，因此原因次數不可加總；不包含漲跌停實際成交限制。</p></main>
}
