import {fmt} from './siteData';
import {dropText, dropTo} from './dropTo';
import {marketBacktest as bt} from './marketBacktestData';

const HORIZONS = [20, 60, 120, 250] as const;
const THRESHOLDS = [160, 155, 150, 145, 140, 135, 130] as const;
type Stat = (typeof bt.stats)[number];

const stat = (kind: string, th: number, h: number) =>
  (bt.stats as readonly Stat[]).find(row => row.kind === kind && row.th === th && row.h === h);
const pct = (value: number | null | undefined) =>
  value == null ? '—' : Math.abs(value) < 0.05 ? '0%' : `${value > 0 ? '+' : ''}${fmt(value, 1)}%`;
const tone = (value: number | null | undefined) =>
  value == null ? 'text-slate-500' : value > 0 ? 'text-rose-300' : value < 0 ? 'text-emerald-300' : 'text-slate-300';

/** Fixed-spec bottom-fishing backtest of the listed-market margin ratio (research only, not advice). */
export default function MarketBacktest({latest}: {latest?: readonly [string, number | null, ...unknown[]]}) {
  const head = stat('ratio', 140, 20)!;
  const drawdown = stat('drawdown', -20, 20);
  // The site curve and the backtest series differ by a small level offset after 2021.
  const current = latest?.[1] == null ? null : latest[1] + bt.siteOffset;
  const below = current == null ? [] : THRESHOLDS.filter(th => current < th);
  const ahead = current == null ? [] : [...THRESHOLDS.filter(th => th < current), 120, 100].filter((th, i, all) => all.indexOf(th) === i);
  return <section id="backtest" className="panel mt-6 overflow-hidden" aria-labelledby="backtest-title">
    <div className="space-y-3 p-4 sm:p-5">
      <p className="text-xs font-semibold tracking-widest text-teal-300">歷史回測 · {bt.window[0]}～{bt.window[1]}</p>
      <h2 id="backtest-title" className="text-lg font-semibold text-white">大盤融資維持率跌破多少，抄底勝率較高？</h2>
      <p className="text-sm leading-7 text-slate-300">
        跌破 <b className="text-white">140%</b> 是唯一通過多重檢定修正的門檻：{bt.events140.length} 次訊號中已滿 20 個交易日的 {head.n} 次，
        {' '}<b className="text-white">{fmt(head.win, 0)}%</b> 上漲，平均報酬 {pct(head.mean)}，比任意一天進場多
        {' '}<b className="text-rose-300">{pct(head.alpha)}</b>（BY 修正後 q = {fmt(head.by_q, 3)}）。
        145～160% 的門檻沒有可靠的超額報酬。
      </p>
      {current != null && <p className="rounded border border-slate-700 bg-slate-900/60 p-3 text-sm text-slate-300">
        目前（{latest![0]}）約 <b className="tabular-nums text-white">{fmt(current, 1)}%</b>
        {below.length ? <>，已低於 {below.map(th => th + '%').join('、')} 門檻。</> : '，高於所有回測門檻。'}
        {below.includes(140) && <span className="text-amber-200"> 歷史上跌破 140% 後仍有再跌的情況，見下方最大回落。</span>}
        {ahead.length > 0 && <span className="mt-1 block text-xs text-slate-400">
          若融資股等幅下跌：{ahead.map(th => `到 ${th}% ${dropText(dropTo(current, th))}`).join('、')}。</span>}
      </p>}
    </div>

    <div className="overflow-x-auto border-t border-slate-800">
      <table className="w-full whitespace-nowrap text-sm">
        <caption className="sr-only">各門檻、各持有天數的勝率與超額報酬</caption>
        <thead><tr>
          <th className="text-left">跌破門檻</th>
          {HORIZONS.map(h => <th key={h}>{h} 日勝率 / 超額</th>)}
        </tr></thead>
        <tbody>
          {THRESHOLDS.map(th => <tr key={th} className={th === 140 ? 'bg-teal-950/40' : ''}>
            <td className="text-left font-semibold text-white">&lt; {th}%<span className="ml-2 text-xs font-normal text-slate-400">{stat('ratio', th, 20)?.n ?? 0} 次</span></td>
            {HORIZONS.map(h => {
              const row = stat('ratio', th, h);
              const passed = row?.by_q != null && row.by_q < 0.05;
              return <td key={h} className="tabular-nums">
                {row ? <>{fmt(row.win, 0)}% / <span className={tone(row.alpha)}>{pct(row.alpha)}</span>
                  {passed && <span className="ml-1 rounded bg-teal-400/20 px-1 text-xs text-teal-200" title={`BY q = ${row.by_q}`}>顯著</span>}</> : '—'}
              </td>;
            })}
          </tr>)}
          <tr className="text-slate-400">
            <td className="text-left">任意一天進場</td>
            {HORIZONS.map(h => <td key={h} className="tabular-nums">{bt.baseline[h].win}% / 平均 {pct(bt.baseline[h].mean)}</td>)}
          </tr>
        </tbody>
      </table>
    </div>

    <div className="space-y-3 border-t border-slate-800 p-4 sm:p-5">
      <h3 className="font-semibold text-white">跌破 140% 的每一次</h3>
      <p className="text-xs leading-5 text-slate-400">訊號日收盤後才知道維持率，於次一交易日收盤買進加權指數；報酬不含股利與交易成本。最大回落為進場後 120 日內的最低點。</p>
    </div>
    <div className="overflow-x-auto">
      <table className="w-full whitespace-nowrap text-sm">
        <thead><tr><th className="text-left">訊號日</th><th>進場日</th>{HORIZONS.map(h => <th key={h}>{h} 日</th>)}<th>120 日內最大回落</th></tr></thead>
        <tbody>{bt.events140.map(row => <tr key={row.signal}>
          <td className="text-left">{row.signal}</td><td>{row.entry ?? '—'}</td>
          {HORIZONS.map(h => { const value = row[`r${h}` as const]; return <td key={h} className={'tabular-nums ' + tone(value)}>{value == null ? '尚未滿期' : pct(value)}</td>; })}
          <td className="tabular-nums text-emerald-300">{row.mae120 == null ? '—' : pct(row.mae120)}</td>
        </tr>)}</tbody>
      </table>
    </div>

    <div className="space-y-3 border-t border-slate-800 p-4 text-sm leading-7 text-slate-300 sm:p-5">
      <p>
        <b className="text-white">與指數回撤比較：</b>加權指數自 250 日高點回撤 20% 的訊號有 {drawdown?.n ?? '—'} 次，20 日勝率 {fmt(drawdown?.win, 0)}%、
        超額 {pct(drawdown?.alpha)}，表現相近；維持率門檻多抓到 2018/10、2024/8 等回撤未達 20% 的急跌，訊號日相同或早 1～3 天。
      </p>
      <details>
        <summary className="cursor-pointer font-semibold text-white">方法與限制</summary>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-slate-400">
          <li>規則在看結果前固定：跌破門檻記一次，回升到門檻 + 10 個百分點以上才重新計數，一段下跌只算一次。</li>
          <li>超額 = 訊號後平均報酬 − 全部交易日同天數平均報酬；p 值用 20,000 次隨機抽取相同次數的進場日，7 個門檻 × 4 個天數一起做 Benjamini–Yekutieli 修正。</li>
          <li>2015～2021 年的大盤維持率由 TEJ 歷史資料逐股重算，2022 年後為本站曲線；兩段口徑相差約 {fmt(Math.abs(bt.siteOffset), 2)} 個百分點，已調整。</li>
          <li>樣本只有 {bt.events140.length} 次（滿 20 日者 {head.n} 次），長天期（120、250 日）重疊多、檢定力低；過去結果不代表未來，本頁為研究資料，不構成投資建議。</li>
        </ul>
      </details>
    </div>
  </section>;
}
