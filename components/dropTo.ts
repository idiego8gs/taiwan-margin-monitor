// With the loan and financed lots fixed, the model ratio moves in proportion to the price:
// ratio_after = ratio_now × (1 + price change). So the price change that reaches a target level is
// target / ratio_now − 1. For the market curve this assumes every financed stock moves by the same %.
export const LEVELS = [150, 140, 130, 120, 100] as const;

/** Price change in percent that takes `ratio` to `target`; ≥ 0 means the ratio is already at or below it. */
export function dropTo(ratio: number | string | null | undefined, target: number): number | null {
  const now = ratio == null ? NaN : Number(ratio);
  return Number.isFinite(now) && now > 0 ? (target / now - 1) * 100 : null;
}

/** Price at which the ratio reaches `target`. */
export function priceAt(close: number | string | null | undefined, ratio: number | string | null | undefined,
                        target: number): number | null {
  const change = dropTo(ratio, target), price = close == null ? NaN : Number(close);
  return change == null || !Number.isFinite(price) ? null : price * (1 + change / 100);
}

export function dropText(change: number | null): string {
  if (change == null) return '—';
  if (change >= 0) return '已低於';
  return `再跌 ${(-change).toFixed(1)}%`;
}
