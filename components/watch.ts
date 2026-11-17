import type {Manifest, Stock} from './siteData';

// Home-page attention rules, in percentage points of the per-stock model ratio.
export const WATCH = {low: 140, drop1: 8, drop5: 15} as const;

export type WatchReason = 'low' | 'drop1' | 'drop5';

export type WatchItem = {
  stock: Stock;
  maintenance: number;
  change1: number | null;
  change5: number | null;
  reasons: WatchReason[];
  warmup: boolean;
};

const value = (text: string | null | undefined) => (text == null ? null : Number(text));
const difference = (now: number, before: number | null) => (before == null ? null : now - before);

export function watchList(manifest: Pick<Manifest, 'stocks' | 'official_dates'>): WatchItem[] {
  const items: WatchItem[] = [];
  for (const stock of manifest.stocks) {
    const latest = stock.latest;
    const official = stock.market ? manifest.official_dates[stock.market] : null;
    const maintenance = value(latest?.maintenance);
    // Only rows from the market's latest verified trading day count as "today".
    if (!latest || !official || latest.date !== official || maintenance == null) continue;
    const change1 = difference(maintenance, value(stock.maintenance_1d_ago));
    const change5 = difference(maintenance, value(stock.maintenance_5d_ago));
    const reasons: WatchReason[] = [];
    if (maintenance < WATCH.low) reasons.push('low');
    if (change1 != null && change1 <= -WATCH.drop1) reasons.push('drop1');
    if (change5 != null && change5 <= -WATCH.drop5) reasons.push('drop5');
    if (reasons.length) items.push({stock, maintenance, change1, change5, reasons, warmup: latest.quality === 'warmup'});
  }
  const drop = (item: WatchItem) => Math.min(item.change1 ?? 0, item.change5 ?? 0);
  return items.sort((a, b) => {
    const lowA = a.reasons.includes('low'), lowB = b.reasons.includes('low');
    if (lowA !== lowB) return lowA ? -1 : 1;
    return lowA ? a.maintenance - b.maintenance : drop(a) - drop(b);
  });
}
