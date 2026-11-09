"""Refresh the portable website seed from dated exchange reports."""
import argparse
import json
import os
import re
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .benchmark import TPEX_URL, URL, parse_report, parse_tpex_report
from .finmind import BASE_URL
from .official_repair import NotPublished, number, parse_margin, parse_prices, urls
from .site_snapshot import MARKETS, json_bytes, read_seed, render, write_seed

TWSE = "https://www.twse.com.tw/rwd/zh/"
TPEX = "https://www.tpex.org.tw/www/zh-tw/bulletin/"
ACTION_REPORTS = {
    "TWSE": (("ex_right", TWSE + "exRight/TWT49U", "資料日期", "股票代號"),
             ("capital_reduction", TWSE + "reducation/TWTAUU", "恢復買賣日期", "股票代號"),
             ("par_value_change", TWSE + "change/TWTB8U", "恢復買賣日期", "股票代號"),
             ("split", TWSE + "split/TWTCAU", "恢復買賣日期", "ETF代號")),
    "TPEx": (("ex_right", TPEX + "exDailyQ", "除權息日期", "代號"),
             ("capital_reduction", TPEX + "revivt", "恢復買賣日期", "股票代號"),
             ("par_value_change", TPEX + "pvChgRslt", "恢復買賣日期", "證券代號"),
             ("split", TPEX + "etfSplitRslt", "恢復買賣日期", "證券代號"),
             ("split", TPEX + "etfRvsRslt", "恢復買賣日期", "證券代號")),
}


class SourceClient:
    def __init__(self):
        self.last = {}

    def get(self, url, market):
        interval = 15 if market == "FinMind" else 3
        for attempt in range(2):
            time.sleep(max(0, interval - (time.monotonic() - self.last.get(market, 0))))
            self.last[market] = time.monotonic()
            try:
                with urlopen(Request(url, headers={"User-Agent": "TaiwanMarginMonitor/1.0 research"}), timeout=40) as response:
                    return response.read().decode("utf-8-sig")
            except HTTPError as exc:
                if attempt or exc.code not in (429, 500, 502, 503, 504):
                    raise
                retry = exc.headers.get("Retry-After", "15")
                if not retry.isdecimal() or int(retry) > 60:
                    # Leave the run retryable at the next scheduled check.
                    raise
                time.sleep(int(retry))


def event_date(raw):
    digits = re.sub(r"[^0-9]", "", raw)
    if len(digits) == 7:
        return date(int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:]))
    if len(digits) == 8:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:]))
    raise ValueError("unexpected corporate-action date")


def action_rows(raw, market, first, last, date_column, code_column):
    obj = json.loads(raw)
    status = obj.get("stat", "")
    if market == "TWSE" and status == "很抱歉，沒有符合條件的資料!":
        return []
    if status.lower() != "ok":
        raise ValueError(f"{market} corporate action report not validated")
    if market == "TPEx":
        if obj.get("date") != f"{first:%Y%m%d}~{last:%Y%m%d}" or len(obj.get("tables", [])) != 1:
            raise ValueError("TPEx corporate action period mismatch")
        table = obj["tables"][0]
    else:
        params = obj.get("params", {})
        if (obj.get("strDate") or obj.get("startDate") or params.get("startDate")) != first.strftime("%Y%m%d") or (
            obj.get("endDate") or params.get("endDate")) != last.strftime("%Y%m%d"):
            raise ValueError("TWSE corporate action period mismatch")
        table = obj
    fields, data = table["fields"], table["data"]
    if len(set(fields)) != len(fields) or date_column not in fields or code_column not in fields:
        raise ValueError("corporate action columns changed")
    if table.get("totalCount", len(data)) != len(data):
        raise ValueError("corporate action report truncated")
    result = []
    for cells in data:
        record = dict(zip(fields, cells, strict=True))
        day = event_date(record[date_column])
        if not first <= day <= last:
            raise ValueError("unexpected effective date")
        result.append((str(day), record[code_column].strip(), record))
    return result


def normalize_action(kind, record, source):
    if kind == "ex_right" and record.get("權/息") in ("息", "除息"):
        cash = number(record.get("現金股利", record.get("權值+息值")))
        if cash is not None and cash > 0:
            return {"kind": "cash_dividend", "cash": str(cash), "multiplier": "1",
                    "reason": None, "source": source}
    # TPEx publishes exact ratios in the detail cell. Never infer one from prices.
    multiplier = None
    detail = record.get("詳細資料", "")
    if kind == "par_value_change":
        match = re.search(r"變更股票面額換股率[:：]</th><td>([0-9.]+)</td>", detail)
        if match:
            multiplier = number(match[1])
    if kind == "capital_reduction" and record.get("減資原因") == "彌補虧損":
        match = re.search(r"每壹仟股換發新股票[:：]</th><td>([0-9.]+)&nbsp股</td>", detail)
        # Limit automatic conversion to a verified pure share reduction.
        if match and "現金增資總股數:</th><td>NA</td>" in detail:
            multiplier = number(match[1]) / Decimal(1000)
    return {"kind": "unhandled_ex_right" if kind == "ex_right" else kind,
            "cash": "0" if multiplier else None, "multiplier": str(multiplier) if multiplier else None,
            "source": source, "reason": None if multiplier else "公司行動換股條款待核對"}


def fetch_actions(client, market, first, last):
    result, evidence = {}, {}
    fmt = "%Y%m%d" if market == "TWSE" else "%Y/%m/%d"
    params = urlencode({"startDate": first.strftime(fmt), "endDate": last.strftime(fmt), "response": "json"})
    for kind, endpoint, date_column, code_column in ACTION_REPORTS[market]:
        url = endpoint + "?" + params
        raw = client.get(url, market)
        evidence[url] = sha256(raw.encode()).hexdigest()
        for day, code, row in action_rows(raw, market, first, last, date_column, code_column):
            key = (day, code)
            event = normalize_action(kind, row, endpoint)
            if key in result and result[key] != event:
                event.update(multiplier=None, cash=None, reason="同日公司行動條款需要合併核對")
                event["kind"] = "multiple_actions"
            result[key] = event
    return result, evidence


def trading_days(client, market, first, last):
    month = first.replace(day=1)
    days = set()
    while month <= last:
        endpoint, parser, token = (URL, parse_report, month.strftime("%Y%m%d")) if market == "TWSE" else (
            TPEX_URL, parse_tpex_report, f"{month.year - 1911:03d}/{month.month:02d}")
        raw = client.get(endpoint + "?" + urlencode({"date": token, "response": "json"}), market)
        days.update(day for day in parser(raw, month.year, month.month) if str(first) <= day <= str(last))
        month = (month + timedelta(days=32)).replace(day=1)
    return days


def daily_reports(client, market, day):
    quote_raw = client.get(urls(market, day, True), market)
    margin_raw = client.get(urls(market, day), market)
    quotes = parse_prices(quote_raw, market, day)
    _, margins, _ = parse_margin(margin_raw, market, day)
    if len(quotes) < 100 or len(margins) < 100:
        raise NotPublished(f"{market} report incomplete")
    return quotes, margins


def finmind_quote(client, code, key):
    url = BASE_URL + "?" + urlencode({"dataset": "TaiwanStockPrice", "data_id": code,
                                     "start_date": key, "end_date": key})
    obj = json.loads(client.get(url, "FinMind"))
    if obj.get("status") != 200 or not isinstance(obj.get("data"), list):
        return None
    rows = [row for row in obj["data"] if row.get("stock_id") == code and row.get("date") == key]
    if len(rows) != 1:
        return None
    row = rows[0]
    fields = dict(open="open", high="max", low="min", close="close", volume="Trading_Volume", turnover="Trading_money")
    quote = {key: number(row.get(field), key in ("volume", "turnover")) for key, field in fields.items()}
    low, op, close, high = (quote[field] for field in ("low", "open", "close", "high"))
    if not all((low, op, close, high)) or not low <= min(op, close) <= max(op, close) <= high:
        return None
    return quote


def apply_day(seed, market, key, quotes, margins, events, client, fallback_budget):
    for stock in seed["stocks"]:
        if stock["market"] != market:
            continue
        code = stock["code"]
        quote, margin = quotes.get(code), margins.get(code)
        price_source = market if quote else None
        if (quote is None or quote.get("close") is None) and margin and fallback_budget[0]:
            fallback_budget[0] -= 1
            try:
                fallback = finmind_quote(client, code, key)
            except (OSError, ValueError):
                fallback = None
            if fallback:
                quote, price_source = fallback, "FinMind"
        stock["rows"][key] = {**(quote or {}), **(margin or {}), "price_source": price_source,
                              "margin_source": market if margin else None,
                              "price_revision": sha256(json_bytes(quote)).hexdigest() if quote else None,
                              "margin_revision": sha256(json_bytes(margin)).hexdigest() if margin else None}
        event = events.get((key, code))
        if event:
            stock["events"][key] = event
        elif stock["events"].get(key, {}).get("source", "").startswith(("https://www.twse.com.tw/", "https://www.tpex.org.tw/")):
            del stock["events"][key]
        stock["checked_action_days"] = sorted(set(stock.get("checked_action_days", ())) | {key})
    seed["calendar"][market] = sorted(set(seed["calendar"][market]) | {key})
    seed["official_dates"][market] = max(seed["official_dates"][market] or key, key)


def refresh(seed, through, client, fallback_limit=6):
    last = seed["official_through"] or max(
        key for stock in seed["stocks"] for key, row in stock["rows"].items() if row.get("close") is not None)
    known = sorted({key for market in MARKETS for key in seed["calendar"][market] if key <= last})
    first = date.fromisoformat(known[-5] if len(known) >= 5 else last)
    if through < first:
        return {"status": "unchanged", "through": last}
    calendars = {market: trading_days(client, market, first, through) for market in MARKETS}
    days = sorted(set.union(*calendars.values()))
    if not days:
        return {"status": "awaiting_trading_day", "through": last}
    end = date.fromisoformat(days[-1])
    action_data = {market: fetch_actions(client, market, first, end) for market in MARKETS}
    budget = [fallback_limit]
    pending = None
    for key in days:
        markets = [market for market in MARKETS if key in calendars[market]]
        if len(markets) != len(MARKETS):
            pending = key
            break
        try:
            reports = {market: daily_reports(client, market, date.fromisoformat(key)) for market in markets}
        except NotPublished:
            pending = key
            # Do not apply half of a day. The date remains eligible on every retry.
            break
        for market, (quotes, margins) in reports.items():
            apply_day(seed, market, key, quotes, margins, action_data[market][0], client, budget)
        seed["official_through"] = max(seed["official_through"] or key, key)
    if seed["official_through"] and not pending:
        seed["action_evidence"] = {market: data[1] for market, data in action_data.items()}
    return {"status": "awaiting_publication" if pending else "checked",
            "through": seed["official_through"], "pending": pending}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--through", type=date.fromisoformat)
    args = parser.parse_args()
    local = datetime.now(ZoneInfo("Asia/Taipei"))
    through = args.through or local.date() - timedelta(days=local.hour < 20)
    if through > local.date():
        parser.error("cannot fetch a future trading date")
    seed = read_seed(args.seed)
    before = sha256(json_bytes(seed)).hexdigest()
    result = refresh(seed, through, SourceClient())
    # Keep the existing publication if neither inputs nor available dates changed.
    if sha256(json_bytes(seed)).hexdigest() != before and seed["official_through"]:
        seed["workflow_run"] = os.getenv("SITE_WORKFLOW_RUN")
    write_seed(args.output, seed)
    result["snapshot_id"] = render(read_seed(args.output), args.root)["snapshot_id"]
    print(json.dumps(result))


if __name__ == "__main__":
    main()
