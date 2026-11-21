"""Official after-close market breadth and intraday order flow, kept in the site checkpoint.

* Limit-up / limit-down counts: TWSE MI_INDEX (type=MS, "漲跌證券數合計", stocks column)
  and TPEx afterTrading/highlight.
* Intraday curve: TWSE MI_5MINS, cumulative buy/sell order and trade quantities and trade
  value every 5 seconds, sampled once a minute. The recent days form the baseline the live
  intraday page compares against at the same time of day.
* Live bottom-fishing signals: the fixed backtest rule applied to the site's market curve
  after the published backtest window.
"""
import argparse
import json
import re
from datetime import date
from pathlib import Path
from statistics import median

from .site_snapshot import json_bytes, read_seed, atomic_write

TWSE_BREADTH = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={day:%Y%m%d}&type=MS&response=json"
TWSE_FLOW = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_5MINS?date={day:%Y%m%d}&response=json"
TPEX_BREADTH = "https://www.tpex.org.tw/www/zh-tw/afterTrading/highlight?date={day:%Y/%m/%d}&response=json"
FLOW_FIELDS = ["時間", "累積委託買進筆數", "累積委託買進數量", "累積委託賣出筆數", "累積委託賣出數量",
               "累積成交筆數", "累積成交數量", "累積成交金額"]
BACKFILL_DAYS = 60
CURVE_DAYS = 20
# The published backtest (components/marketBacktestData.ts) ends here; later signals are listed live.
BACKTEST_END = "2026-11-16"
THRESHOLDS = (160, 155, 150, 145, 140, 135, 130)
HORIZONS = (20, 60, 120, 250)


def integer(text):
    digits = re.sub(r"[^0-9]", "", str(text))
    if not digits:
        raise ValueError("missing breadth number")
    return int(digits)


def count_pair(text):
    """'1,058(179)' -> (1058, 179)."""
    match = re.fullmatch(r"\s*([0-9,]+)\s*\(\s*([0-9,]+)\s*\)\s*", str(text))
    if not match:
        raise ValueError("unexpected breadth cell")
    return integer(match[1]), integer(match[2])


def parse_twse_breadth(raw, day):
    obj = json.loads(raw)
    if str(obj.get("stat", "")).upper() != "OK":
        return None
    table = next((t for t in obj.get("tables", []) if t.get("title") == "漲跌證券數合計"), None)
    if not table or table.get("fields") != ["類型", "整體市場", "股票"]:
        raise ValueError("TWSE breadth table changed")
    rows = {row[0]: row[2] for row in table["data"]}
    up, limit_up = count_pair(rows["上漲(漲停)"])
    down, limit_down = count_pair(rows["下跌(跌停)"])
    return {"up": up, "limit_up": limit_up, "down": down, "limit_down": limit_down, "flat": integer(rows["持平"])}


def parse_tpex_breadth(raw, day):
    obj = json.loads(raw)
    if str(obj.get("stat", "")).lower() != "ok" or not obj.get("tables"):
        return None
    if obj.get("date") != f"{day:%Y%m%d}":
        raise ValueError("TPEx breadth date mismatch")
    table = obj["tables"][0]
    if not table.get("data"):
        return None
    row = dict(zip(table["fields"], table["data"][0], strict=True))
    return {"up": integer(row["上漲家數"]), "limit_up": integer(row["漲停家數"]),
            "down": integer(row["下跌家數"]), "limit_down": integer(row["跌停家數"]),
            "flat": integer(row["平盤家數"])}


def parse_twse_flow(raw):
    """Minute samples [minute since 09:00, buy qty, sell qty, trade qty, trade value (NT$ million)]."""
    obj = json.loads(raw)
    if str(obj.get("stat", "")).upper() != "OK":
        return None
    if obj.get("fields") != FLOW_FIELDS:
        raise ValueError("TWSE MI_5MINS columns changed")
    curve = {}
    for row in obj["data"]:
        hour, minute, second = (int(part) for part in row[0].split(":"))
        if second != 0:
            continue
        index = (hour - 9) * 60 + minute
        if 0 <= index <= 270:
            curve[index] = [index, integer(row[2]), integer(row[4]), integer(row[6]), integer(row[7])]
    if len(curve) < 200:
        raise ValueError("TWSE MI_5MINS incomplete")
    return [curve[key] for key in sorted(curve)]


def refresh(seed, client, through=None):
    """Fetch breadth and the TWSE minute curve for checked trading days that are not stored yet."""
    breadth = seed.setdefault("breadth", {})
    curves = seed.setdefault("intraday_curves", {})
    days = [day for day in seed["calendar"].get("TWSE", []) if day <= (through or seed["official_through"] or "")]
    pending = [day for day in days[-BACKFILL_DAYS:] if day not in breadth]
    added = 0
    for key in pending:
        day = date.fromisoformat(key)
        twse = parse_twse_breadth(client.get(TWSE_BREADTH.format(day=day), "TWSE"), day)
        tpex = parse_tpex_breadth(client.get(TPEX_BREADTH.format(day=day), "TPEx"), day)
        if not twse or not tpex:
            continue  # not published yet; retried by the next run
        breadth[key] = {"TWSE": twse, "TPEx": tpex}
        if key in days[-CURVE_DAYS:]:
            curve = parse_twse_flow(client.get(TWSE_FLOW.format(day=day), "TWSE"))
            if curve:
                curves[key] = curve
        added += 1
    for key in sorted(curves)[:-CURVE_DAYS]:
        del curves[key]
    return {"breadth_added": added, "breadth_through": max(breadth) if breadth else None}


def baseline(curves):
    """Median cumulative trade value and order quantities at each minute across stored days."""
    days = sorted(curves)
    if not days:
        return None
    by_minute = {}
    for key in days:
        for index, buy, sell, qty, value in curves[key]:
            by_minute.setdefault(index, []).append((buy, sell, qty, value))
    minutes = sorted(by_minute)
    column = lambda i: [median(sample[i] for sample in by_minute[m]) for m in minutes]
    return {"days": days, "minutes": minutes, "buy": column(0), "sell": column(1), "qty": column(2), "value": column(3),
            "latest": {"date": days[-1], "curve": curves[days[-1]]}}


def events(series, threshold, rearm=10):
    """Bottom-fishing rule: below threshold while armed; re-armed only at threshold + rearm or higher."""
    out, armed = [], True
    for index, value in enumerate(series):
        if value is None:
            continue
        if value >= threshold + rearm:
            armed = True
        if armed and value < threshold:
            out.append(index)
            armed = False
    return out


def live_signals(market_rows, taiex):
    """Signals after the published backtest window, with realised TAIEX returns so far."""
    days = [row[0] for row in market_rows]
    series = [row[1] for row in market_rows]
    closes = {day: float(value) for day, value in taiex.items()}
    trading = sorted(closes)
    result = []
    for threshold in THRESHOLDS:
        for index in events(series, threshold):
            signal = days[index]
            if signal <= BACKTEST_END:
                continue
            later = [day for day in trading if day > signal]
            entry = later[0] if later else None
            returns = {}
            for horizon in HORIZONS:
                exit_day = later[horizon] if len(later) > horizon else None
                returns[f"r{horizon}"] = (round((closes[exit_day] / closes[entry] - 1) * 100, 1)
                                          if entry and exit_day else None)
            result.append({"threshold": threshold, "signal": signal, "ratio": round(series[index], 2),
                           "entry": entry, **returns})
    return {"after": BACKTEST_END, "events": result}


def export(seed, market_history, output):
    output.mkdir(parents=True, exist_ok=True)
    breadth = seed.get("breadth", {})
    rows = [[day, *(breadth[day][market][field] for market in ("TWSE", "TPEx")
                    for field in ("up", "limit_up", "down", "limit_down", "flat"))] for day in sorted(breadth)]
    atomic_write(output / "market-breadth.json", json_bytes({
        "version": 1, "columns": ["date"] + [f"{market}_{field}" for market in ("TWSE", "TPEx")
                                             for field in ("up", "limit_up", "down", "limit_down", "flat")],
        "rows": rows, "source": "TWSE MI_INDEX 漲跌證券數合計（股票）; TPEx 上櫃股票當日彙總資訊"}))
    atomic_write(output / "intraday-baseline.json", json_bytes({
        "version": 1, "unit": {"buy": "張", "sell": "張", "qty": "張", "value": "百萬元"},
        "baseline": baseline(seed.get("intraday_curves", {})), "source": "TWSE MI_5MINS"}))
    if market_history is not None:
        atomic_write(output / "market-backtest-live.json",
                     json_bytes(live_signals(market_history["rows"], seed.get("taiex_close", {}))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    out = sub.add_parser("export")
    out.add_argument("--seed", type=Path, required=True)
    out.add_argument("--market", type=Path, help="exported wantgoo-market.json for live signals")
    out.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    market = json.loads(args.market.read_bytes()) if args.market and args.market.is_file() else None
    export(read_seed(args.seed), market, args.output)
    print(json.dumps({"exported": str(args.output)}))


if __name__ == "__main__":
    main()
