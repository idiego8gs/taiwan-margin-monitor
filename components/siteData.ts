export const isPages = process.env.NEXT_PUBLIC_PAGES === '1';
export const base = isPages ? '/taiwan-margin-monitor' : '';
const api = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export type Day = {
  date: string;
  open: string | null;
  high: string | null;
  low: string | null;
  close: string | null;
  volume: number | null;
  balance: number | null;
  cost: string | null;
  maintenance: string | null;
  reference_130: string | null;
  segment: number | null;
  warmup: boolean;
  quality: string;
  price_source: string | null;
  margin_source: string | null;
  action: {kind: string; cash: string | null; source: string; reason: string | null} | null;
};

export type Stock = {
  security_id: number;
  code: string;
  name: string;
  market: string | null;
  rank: number;
  latest_quote_date: string | null;
  latest_estimate_date: string | null;
  latest: Day | null;
};

export type Manifest = {
  snapshot_id: string;
  model: string;
  universe_date: string;
  created_at: string;
  official_through: string | null;
  official_dates: Record<string, string | null>;
  workflow_run: string | null;
  review_count: number;
  stocks: Stock[];
};

export const labels: Record<string, string> = {
  estimated: '研究估算',
  warmup: '暖機估算',
  not_applicable: '無融資餘額',
  corporate_action_coverage_gap: '公司行動待核',
  missing_price: '行情缺漏',
  missing_margin_data: '融資資料缺漏',
  previous_balance_conflict: '餘額斷點',
  balance_identity_mismatch: '當日餘額不符',
  missing_trading_dates: '交易日斷點',
  date_not_in_calendar: '交易日待核',
  unhandled_share_action: '換股條款待核',
  unhandled_ex_right: '除權條款待核',
  par_value_change: '面額變更條款待核',
  capital_reduction: '減資條款待核',
  invalid_cost_input: '成本計算資料異常',
  insufficient_data: '資料不足',
  missing_dividend_result: '除息結果待核',
};

export async function getJSON<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error('資料載入失敗，請確認快照版本或稍後重試。');
  return response.json();
}

export async function loadIndex(version?: string) {
  if (!isPages) {
    return getJSON<Manifest>(`${api}/api/v2/stocks${version ? '?snapshot_id=' + encodeURIComponent(version) : ''}`);
  }
  const snapshot = version || (await getJSON<{snapshot_id: string}>(`${base}/data/latest.json`)).snapshot_id;
  if (!/^[a-f0-9]{64}$/.test(snapshot)) throw new Error('快照版本無效');
  return getJSON<Manifest>(`${base}/data/snapshots/${snapshot}/index.json`);
}

export function loadHistory(snapshot: string, id: number) {
  const url = isPages
    ? `${base}/data/snapshots/${snapshot}/stocks/${id}.json`
    : `${api}/api/v2/stocks/${id}/history?snapshot_id=${snapshot}`;
  return getJSON<{snapshot_id: string; stock: Stock; items: Day[]}>(url);
}

export function stockLink(stock: Stock, snapshot: string) {
  return `${base}/stocks/s-${stock.security_id}/?snapshot=${snapshot}`;
}

export const fmt = (value: string | number | null | undefined, digits = 2) =>
  value == null ? '—' : Number(value).toLocaleString('zh-TW', {maximumFractionDigits: digits});

export function csvDownload(name: string, rows: (string | number | null | undefined)[][]) {
  const content = rows.map(row => row.map(cell => '"' + String(cell ?? '').replaceAll('"', '""') + '"').join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob(['\ufeff', content], {type: 'text/csv;charset=utf-8'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}
