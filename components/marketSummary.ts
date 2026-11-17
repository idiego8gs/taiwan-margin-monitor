export type MarketSummary = {date: string; value: number; change: number | null; spark: number[]};

/** Latest market ratio, its change from the previous estimate, and up to 60 recent points. */
export function marketSummary(rows: readonly (readonly [string, number | null, ...unknown[]])[]): MarketSummary | null {
  const points = rows.filter(row => row[1] != null) as (readonly [string, number])[];
  if (!points.length) return null;
  const [date, value] = points[points.length - 1];
  const previous = points.length > 1 ? points[points.length - 2][1] : null;
  return {date, value, change: previous == null ? null : value - previous, spark: points.slice(-60).map(row => row[1])};
}
