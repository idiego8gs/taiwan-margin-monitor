"""Ex-right events (stock dividends, rights offerings) for the site principal model.

Stock dividends and subscription rights are delivered to the investor's own
depository account. The exchange's margin balance and the loan are not
converted: published balances show no residual on the ex-right or allotment
day. The financed position therefore keeps its lots and its average cost,
exactly as a cash dividend does in the principal model, and the ratio
reflects the lower post-ex-right price.

An event is accepted only when its published terms reproduce the exchange's
reference price: reference = (previous close - cash) / (1 + bonus shares per share).
Prices are used to check terms, never to infer them. Anything else stays
``unhandled_ex_right`` and restarts the cost segment as before.
"""
from decimal import Decimal, InvalidOperation
import re

KIND = "stock_dividend"
TOLERANCE = (Decimal("0.01"), Decimal("0.005"))


def decimal(value):
    if value is None:
        return None
    try:
        result = Decimal(re.sub(r"[^0-9.\-]", "", str(value)) or "x")
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def reproduces_reference(before, reference, cash, bonus_per_thousand):
    """True when the dividend terms explain the exchange's ex-right reference price."""
    values = (before, reference, cash, bonus_per_thousand)
    if any(value is None for value in values) or before <= 0 or reference <= 0 or cash < 0 or bonus_per_thousand < 0:
        return False
    expected = (before - cash) / (1 + Decimal(bonus_per_thousand) / 1000)
    return abs(expected - reference) <= max(TOLERANCE[0], reference * TOLERANCE[1])


def record(source, cash):
    # multiplier "1": the financed lots and loan are unchanged by the event.
    return {"kind": KIND, "source": source, "reason": None, "multiplier": "1", "cash": str(cash)}


def from_finmind(day, policy_rows, result_row, source):
    """Resolve one FinMind TaiwanStockDividendResult ex-right row, or return None."""
    key = str(day)
    stock = [row for row in policy_rows if row.get("StockExDividendTradingDate") == key]
    if len(stock) != 1 or result_row is None:
        return None
    # FinMind publishes bonus shares as NT$ of par per share; par is assumed to be NT$10
    # and the reference-price check rejects every event where that assumption fails.
    amount = (decimal(stock[0].get("StockEarningsDistribution")) or Decimal(0)) + (
        decimal(stock[0].get("StockStatutorySurplus")) or Decimal(0))
    cash = sum(((decimal(row.get("CashEarningsDistribution")) or Decimal(0)) + (decimal(row.get("CashStatutorySurplus")) or 0)
                for row in policy_rows if row.get("CashExDividendTradingDate") == key), Decimal(0))
    if not reproduces_reference(decimal(result_row.get("before_price")), decimal(result_row.get("reference_price")),
                                cash, amount * 100):
        return None
    return record(source, cash)


def from_tpex(row, source):
    """TPEx exDailyQ rows carry the exact bonus-share and cash terms."""
    cash, bonus = decimal(row.get("現金股利")), decimal(row.get("每仟股無償配股"))
    if not reproduces_reference(decimal(row.get("除權息前收盤價")), decimal(row.get("除權息參考價")), cash, bonus):
        return None
    return record(source, cash)


def twse_detail_terms(raw):
    """Parse TWSE TWT49UDetail into (cash per share, bonus shares per thousand)."""
    import json
    obj = json.loads(raw)
    if str(obj.get("stat", "")).lower() != "ok" or len(obj.get("data", [])) != 1:
        return None
    detail = dict(zip(obj["fields"], obj["data"][0], strict=True))
    cash = next((decimal(v) for k, v in detail.items() if "每股配發現金股利" in k), None)
    bonus = next((decimal(v) for k, v in detail.items() if "每千股無償配股" in k), None)
    return (cash, bonus) if cash is not None and bonus is not None else None


def from_twse(row, terms, source):
    if not terms:
        return None
    cash, bonus = terms
    if not reproduces_reference(decimal(row.get("除權息前收盤價")), decimal(row.get("除權息參考價")), cash, bonus):
        return None
    return record(source, cash)
