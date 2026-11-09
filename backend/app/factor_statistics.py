"""Point-in-time matching and date-block inference for exploratory factor research.

Callers must establish actual publication/availability timestamps, point-in-time
universe membership, and valid transaction-adjusted forward returns themselves.
An asserted timestamp is not proof of publication; unknown timestamps should
not be fabricated. These utilities do not establish causality or validate OOS.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from math import ceil, isfinite, sqrt
from random import Random
from statistics import stdev
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Snapshot:
    """Information associated with one security at one decision time.

    ``features_available_at`` must represent the *latest actual availability*
    of the features, market, industry and universe eligibility information;
    timestamps must be timezone-aware. Missing availability is accepted only
    in explicitly unverified exploratory scenarios. ``outcome_pct`` is a separately
    computed forward net return in percent, not available at decision time.
    Selection never checks or ranks on that outcome.
    """

    code: str
    signal_date: date
    market: str
    industry: str | None
    decision_at: datetime
    features_available_at: datetime | None
    features: Mapping[str, float | None]
    outcome_pct: float | None
    entry_date: date | None = None
    exit_date: date | None = None


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _finite_number(value: object) -> bool:
    try:
        return not isinstance(value, bool) and value is not None and isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _validate_snapshot(row: Snapshot, allow_unverified_availability: bool = False) -> None:
    if not row.code or not row.market or not isinstance(row.signal_date, date) or isinstance(row.signal_date, datetime):
        raise ValueError('security code, market, and a date are required')
    if not _aware(row.decision_at) or (not _aware(row.features_available_at) and not (allow_unverified_availability and row.features_available_at is None)):
        raise ValueError('actual availability and decision timestamps must be timezone-aware')
    if row.decision_at.astimezone(ZoneInfo('Asia/Taipei')).date() != row.signal_date:
        raise ValueError('signal date must agree with the Taiwan decision date')
    if row.features_available_at is not None and row.features_available_at > row.decision_at:
        raise ValueError('features were not available at the decision timestamp')
    if not isinstance(row.features, Mapping):
        raise ValueError('features must be a mapping')


def match_on_date(
    signals: Sequence[Snapshot],
    controls: Sequence[Snapshot],
    calipers: Mapping[str, float],
    *,
    require_same_industry: bool = True,
    allow_unverified_availability: bool = False,
) -> dict:
    """Find one contemporaneous control by predeclared feature calipers.

    Market and signal date must match; industry also matches by default.
    For *each* specified feature, absolute difference must not exceed its
    fixed caliper. Rank qualifying controls by mean squared scaled difference,
    break ties by code. All security codes signalled on the same date are
    excluded from that date's controls. Reuse of controls across signals is
    allowed. Matching is performed before checking outcomes: when the best
    match lacks a future outcome, the pair is dropped, not rematched ex post.
    Both outcomes must have the same explicitly documented entry and exit
    dates; missing or mismatched windows are excluded after selection.

    Returns pairs and attrition counters. Lack of observed features/industry
    is a missing-data exclusion, never implicit imputation. A contemporaneous
    market cap or industry still requires independent PIT provenance.
    ``allow_unverified_availability`` is diagnostic only and never proves PIT.
    """

    if not calipers or any(not isinstance(k, str) or not k or not _finite_number(v) or float(v) <= 0 for k, v in calipers.items()):
        raise ValueError('nonempty, positive finite predeclared feature calipers are required')
    for row in (*signals, *controls):
        _validate_snapshot(row, allow_unverified_availability)

    def unique(rows: Sequence[Snapshot], name: str) -> None:
        keys = [(r.signal_date, r.code) for r in rows]
        if len(keys) != len(set(keys)):
            raise ValueError(f'duplicate {name} security/date')

    unique(signals, 'signal')
    unique(controls, 'control')
    signalled = defaultdict(set)
    for row in signals:
        signalled[row.signal_date].add(row.code)
    by_date_market = defaultdict(list)
    for row in controls:
        by_date_market[(row.signal_date, row.market)].append(row)
    matches = []
    attrition = Counter(total_signals=len(signals))
    for signal in signals:
        if require_same_industry and not signal.industry:
            attrition['missing_signal_industry'] += 1
            continue
        if any(not _finite_number(signal.features.get(k)) for k in calipers):
            attrition['missing_signal_features'] += 1
            continue
        candidates = []
        for control in by_date_market[(signal.signal_date, signal.market)]:
            if control.code in signalled[signal.signal_date]:
                continue
            if control.features_available_at is not None and control.features_available_at > signal.decision_at:
                continue
            if require_same_industry and (not control.industry or control.industry != signal.industry):
                continue
            if any(not _finite_number(control.features.get(k)) for k in calipers):
                continue
            scaled = [(float(signal.features[k]) - float(control.features[k])) / float(width) for k, width in calipers.items()]
            if any(abs(value) > 1 for value in scaled):
                continue
            distance = sum(value * value for value in scaled) / len(scaled)
            candidates.append((distance, control.code, control))
        if not candidates:
            attrition['no_eligible_control'] += 1
            continue
        distance, _, best = min(candidates, key=lambda x: (x[0], x[1]))
        if not _finite_number(signal.outcome_pct):
            attrition['missing_signal_outcome_after_selection'] += 1
            continue
        if not _finite_number(best.outcome_pct):
            attrition['missing_control_outcome_after_selection'] += 1
            continue
        windows = (signal.entry_date, signal.exit_date, best.entry_date, best.exit_date)
        if any(not isinstance(value, date) or isinstance(value, datetime) for value in windows):
            attrition['missing_outcome_window_after_selection'] += 1
            continue
        if (signal.entry_date != best.entry_date or signal.exit_date != best.exit_date or
                signal.entry_date <= signal.signal_date or signal.exit_date < signal.entry_date):
            attrition['invalid_or_mismatched_outcome_window_after_selection'] += 1
            continue
        matches.append({
            'signal_date': signal.signal_date, 'signal_code': signal.code,
            'control_code': best.code, 'market': signal.market,
            'industry': signal.industry, 'distance': distance,
            'entry_date': signal.entry_date, 'exit_date': signal.exit_date,
            'signal_net_pct': float(signal.outcome_pct),
            'control_net_pct': float(best.outcome_pct),
            'matched_excess_pp': float(signal.outcome_pct) - float(best.outcome_pct),
        })
    attrition['matched'] = len(matches)
    return {'matches': matches, 'attrition': dict(attrition),
            'feature_calipers': dict(calipers), 'same_industry_required': require_same_industry,
            'availability_unverified_scenario': allow_unverified_availability,
            'limitations': ['matching is observational and does not prove causality',
                'caller must verify timestamps, historical constituents and forward execution',
                'controls may be reused; standard errors must account for date clustering']}


def _percentile(sorted_values: list[float], fraction: float) -> float:
    position = (len(sorted_values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (position - lower) * (sorted_values[upper] - sorted_values[lower])


def moving_block_inference(
    matches: Sequence[Mapping], trading_calendar: Sequence[date], *,
    block_days: int, draws: int = 5000, seed: int = 20261104,
    min_signal_dates: int = 10,
) -> dict:
    """Moving-block bootstrap of all matched excess returns on calendar days.

    The caller supplies the complete, verified chronological trading calendar
    covering all observed events, including days *without* signals. Consecutive
    (non-wrapping) calendar blocks are sampled until the original calendar
    length is reached; all same-date events travel together. The statistic is
    event-weighted. A percentile 95% interval, centered two-sided bootstrap
    p-value and bootstrap standard error are returned, not a causal test.
    Serial dependence beyond the chosen block, nonstationarity and few event
    dates may invalidate this approximation. Fix block length before testing.
    """

    calendar = list(trading_calendar)
    if not calendar or any(not isinstance(d, date) or isinstance(d, datetime) for d in calendar):
        raise ValueError('verified date-only trading calendar required')
    if calendar != sorted(set(calendar)):
        raise ValueError('trading calendar must be unique and chronological')
    if not isinstance(block_days, int) or isinstance(block_days, bool) or not 1 <= block_days <= len(calendar):
        raise ValueError('block_days must be within the trading calendar')
    if not isinstance(draws, int) or isinstance(draws, bool) or draws < 500:
        raise ValueError('at least 500 bootstrap draws required')
    if not isinstance(min_signal_dates, int) or min_signal_dates < 2:
        raise ValueError('minimum signal dates must be at least two')
    index = {d: i for i, d in enumerate(calendar)}
    daily_sum = [0.0] * len(calendar)
    daily_count = [0] * len(calendar)
    event_dates = set()
    seen = set()
    for item in matches:
        day = item.get('signal_date')
        value = item.get('matched_excess_pp')
        if day not in index or not _finite_number(value):
            raise ValueError('every matched outcome requires a finite value and calendar date')
        if 'signal_code' in item:
            key = (day, item['signal_code'])
            if key in seen:
                raise ValueError('duplicate signal on the same date')
            seen.add(key)
        pos = index[day]
        daily_sum[pos] += float(value)
        daily_count[pos] += 1
        event_dates.add(day)
    base = {'observations': len(matches), 'signal_dates': len(event_dates),
            'calendar_days': len(calendar), 'block_days': block_days,
            'draws_requested': draws, 'seed': seed}
    if len(event_dates) < min_signal_dates or len(calendar) < 2 * block_days:
        return {**base, 'status': 'insufficient_data',
                'reason': 'too few distinct signal dates or too short a calendar',
                'mean_excess_pp': None, 'ci95_pp': None, 'p_value': None, 't_stat': None}
    observed = sum(daily_sum) / sum(daily_count)
    # Prefix sums preserve contiguous date blocks while avoiding a linear sum
    # over every block on every resample.  This matters for multi-year runs.
    prefix_sum = [0.0]
    prefix_count = [0]
    for value, count in zip(daily_sum, daily_count):
        prefix_sum.append(prefix_sum[-1] + value)
        prefix_count.append(prefix_count[-1] + count)
    rng = Random(seed)
    samples = []
    starts = len(calendar) - block_days + 1
    blocks_needed = ceil(len(calendar) / block_days)
    for _ in range(draws):
        chosen = [rng.randrange(starts) for _ in range(blocks_needed)]
        total, count, remaining = 0.0, 0, len(calendar)
        for start in chosen:
            width = min(block_days, remaining)
            total += prefix_sum[start + width] - prefix_sum[start]
            count += prefix_count[start + width] - prefix_count[start]
            remaining -= width
        if count:
            samples.append(total / count)
    if len(samples) < draws * .95:
        return {**base, 'status': 'insufficient_data',
                'reason': 'too many resamples have no signal events',
                'mean_excess_pp': observed, 'ci95_pp': None, 'p_value': None, 't_stat': None}
    standard_error = stdev(samples)
    if standard_error <= 1e-15:
        return {**base, 'status': 'insufficient_variation',
                'reason': 'bootstrap variation is zero; inference is undefined',
                'mean_excess_pp': observed, 'ci95_pp': None, 'p_value': None, 't_stat': None}
    centered_p = (1 + sum(abs(sample - observed) >= abs(observed) for sample in samples)) / (len(samples) + 1)
    samples.sort()
    return {**base, 'status': 'ok', 'mean_excess_pp': observed,
            'ci95_pp': [_percentile(samples, .025), _percentile(samples, .975)],
            'bootstrap_se_pp': standard_error, 't_stat': observed / standard_error,
            'p_value': centered_p, 'valid_draws': len(samples),
            'notes': ['approximate event-weighted moving-block bootstrap',
                'complete calendar and predeclared block length required',
                'not risk-adjusted alpha, causal identification, or a true holdout']}


def adjust_pvalues(pvalues: Sequence[float | None], *, method: str = 'BH', planned_tests: int | None = None) -> list[float | None]:
    """Adjust an entire planned family with BH or dependence-robust BY.

    Untestable tests (None) and omitted tests up to ``planned_tests`` count as
    p=1; their reported adjusted p-value remains None. BH controls FDR under
    independence/appropriate positive dependence, while BY is more conservative
    under arbitrary dependence. Neither method repairs biased p-values.
    """

    method = method.upper()
    if method not in ('BH', 'BY'):
        raise ValueError('method must be BH or BY')
    if any(p is not None and (not _finite_number(p) or not 0 <= float(p) <= 1) for p in pvalues):
        raise ValueError('p-values must be in [0, 1] or None')
    count = len(pvalues) if planned_tests is None else planned_tests
    if not isinstance(count, int) or isinstance(count, bool) or count < len(pvalues):
        raise ValueError('planned_tests must include every supplied p-value')
    if count == 0:
        return []
    expanded = [1.0 if p is None else float(p) for p in pvalues] + [1.0] * (count - len(pvalues))
    order = sorted(range(count), key=lambda i: expanded[i])
    correction = sum(1 / i for i in range(1, count + 1)) if method == 'BY' else 1.0
    adjusted = [1.0] * count
    current = 1.0
    for rank in range(count, 0, -1):
        i = order[rank - 1]
        current = min(current, expanded[i] * count * correction / rank)
        adjusted[i] = current
    return [None if p is None else adjusted[i] for i, p in enumerate(pvalues)]
