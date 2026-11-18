import json
from datetime import date
from decimal import Decimal as D

from app import site_update, stock_dividends
from app.site_snapshot import estimate_history


def test_reference_price_check_accepts_only_matching_terms():
    # 富邦金 2025-09-25: 25 bonus shares per thousand, no cash.
    assert stock_dividends.reproduces_reference(D("89"), D("86.82"), D(0), D(25))
    # Same event if par were NT$5 (50 per thousand) does not reproduce the price.
    assert not stock_dividends.reproduces_reference(D("89"), D("86.82"), D(0), D(50))
    assert not stock_dividends.reproduces_reference(None, D("86.82"), D(0), D(25))


def test_finmind_terms_resolve_stock_and_rights_events():
    policy = [{"StockExDividendTradingDate": "2025-09-25", "StockStatutorySurplus": 0.25},
              {"StockExDividendTradingDate": "2025-11-14", "CashIncreaseSubscriptionRate": 2.25}]
    result = {"before_price": 89.0, "reference_price": 86.82}
    event = stock_dividends.from_finmind(date(2025, 9, 25), policy, result, "FinMind")
    assert event == {"kind": "stock_dividend", "source": "FinMind", "reason": None, "multiplier": "1", "cash": "0"}
    # A rights offering leaves the reference price at the prior close.
    assert stock_dividends.from_finmind(date(2025, 11, 14), policy, {"before_price": 178.5, "reference_price": 178.5}, "F")
    assert stock_dividends.from_finmind(date(2025, 9, 25), policy, {"before_price": 89.0, "reference_price": 80}, "F") is None
    assert stock_dividends.from_finmind(date(2025, 9, 26), policy, result, "F") is None


def test_official_rows_resolve_with_exact_terms():
    tpex = {"權/息": "除權息", "除權息前收盤價": "34.35", "除權息參考價": "30.74", "現金股利": "1.785", "每仟股無償配股": "59.5"}
    assert site_update.normalize_action("ex_right", tpex, "TPEx")["kind"] == "stock_dividend"
    detail = json.dumps({"stat": "ok", "fields": ["(每股配發現金股利)除息", "A. 按普通股股東持股比例每千股無償配股"],
                         "data": [["0 元／股", "25 股"]]})
    twse = {"權/息": "權", "除權息前收盤價": "89.00", "除權息參考價": "86.82"}
    terms = stock_dividends.twse_detail_terms(detail)
    assert site_update.normalize_action("ex_right", twse, "TWSE", terms)["cash"] == "0"
    assert site_update.normalize_action("ex_right", twse, "TWSE")["kind"] == "unhandled_ex_right"


def row(close, previous, buy, sell, repayment, balance):
    return {"close": close, "open": close, "high": close, "low": close, "volume": 1000,
            "previous_balance": previous, "margin_buy": buy, "margin_sell": sell,
            "repayment": repayment, "balance": balance}


def test_verified_stock_dividend_keeps_cost_segment():
    days = [f"2026-11-0{n}" for n in range(2, 7)]
    rows = {day: row("100", 20, 0, 0, 0, 20) for day in days}
    rows[days[0]] = row("100", 0, 20, 0, 0, 20)
    rows[days[3]] = row("95", 20, 0, 0, 0, 20)
    event = stock_dividends.record("test", D(0))
    stock = {"rows": rows, "events": {days[3]: event}, "action_through": days[-1]}
    items = estimate_history(stock, days)
    assert {item["segment"] for item in items} == {1}
    assert items[3]["cost"] == items[2]["cost"]
    unresolved = {**event, "kind": "unhandled_ex_right", "multiplier": None, "cash": None}
    assert len({item["segment"] for item in estimate_history({**stock, "events": {days[3]: unresolved}}, days)} - {None}) == 2
