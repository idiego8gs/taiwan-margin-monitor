import json
from datetime import date
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app import site_update
from app.main import app
from app.site_snapshot import estimate_history, render


def row(close, previous, buy, sell, repayment, balance):
    return {"open": close, "high": close, "low": close, "close": close,
            "previous_balance": previous, "margin_buy": buy, "margin_sell": sell,
            "repayment": repayment, "balance": balance, "volume": 1000, "turnover": "100000"}


def stock(rows, events=None):
    return {"security_id": 12, "code": "2330", "name": "台積電", "market": "TWSE", "rank": 1,
            "rows": rows, "events": events or {}, "action_through": "2026-11-06"}


def test_close_cost_dividend_split_and_zero():
    dates = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]
    rows = dict(zip(dates, [
        row("100", 0, 100, 0, 0, 100), row("120", 100, 20, 10, 10, 100),
        row("95", 100, 0, 0, 0, 100), row("50", 200, 0, 0, 0, 200),
        row("50", 200, 0, 200, 0, 0),
    ]))
    events = {
        dates[2]: {"kind": "cash_dividend", "cash": "5", "multiplier": "1", "source": "test", "reason": None},
        dates[3]: {"kind": "split", "cash": "0", "multiplier": "2", "source": "test", "reason": None},
    }
    first, second, ex_date, split, zero = estimate_history(stock(rows, events), dates)
    assert first["cost"] == D(100)
    assert second["cost"] == D(104)
    assert ex_date["cost"] == D(104)
    assert ex_date["action"]["cash"] == "5"
    assert split["cost"] == D(52)
    assert split["reference_130"] == D("40.56")
    assert zero["quality"] == "not_applicable"
    assert zero["maintenance"] is None and zero["close"] == "50"


def test_missing_date_breaks_cost_and_restarts_with_warmup():
    a, b, c = "2026-11-02", "2026-11-03", "2026-11-04"
    days = estimate_history(stock({a: row("100", 0, 20, 0, 0, 20),
                                   c: row("70", 20, 1, 0, 0, 21)}), [a, b, c])
    assert [item["date"] for item in days] == [a, b, c]
    assert days[1]["maintenance"] is None
    assert days[2]["segment"] != days[0]["segment"]
    assert days[2]["warmup"]


def test_snapshot_immutable_history_api_matches_static(tmp_path, monkeypatch):
    a, b = "2026-11-02", "2026-11-03"
    seed = {"universe_date": a, "official_through": b, "official_dates": {"TWSE": b, "TPEx": b},
            "calendar": {"TWSE": [a, b], "TPEx": []},
            "stocks": [stock({a: row("100", 0, 20, 0, 0, 20), b: row("90", 20, 1, 0, 0, 21)})]}
    original = render(seed, tmp_path)
    sid = original["snapshot_id"]
    seed["stocks"][0]["rows"][b]["close"] = "91"
    revised = render(seed, tmp_path)
    assert revised["snapshot_id"] != sid
    assert render(seed, tmp_path) == revised
    monkeypatch.setenv("MARGIN_SITE_ROOT", str(tmp_path))
    client = TestClient(app)
    assert client.get("/api/v2/stocks").json()["snapshot_id"] == revised["snapshot_id"]
    static = tmp_path / "snapshots" / sid / "stocks" / "12.json"
    response = client.get(f"/api/v2/stocks/12/history?snapshot_id={sid}")
    assert response.content == static.read_bytes()
    assert original["stocks"][0]["latest"] == json.loads(response.content)["items"][-1]
    date_filtered = client.get(f"/api/v2/stocks/12/history?snapshot_id={sid}&start={b}").json()
    assert [item["date"] for item in date_filtered["items"]] == [b]
    assert client.get("/api/v2/stocks/13/history").status_code == 404
    assert client.get("/api/v2/stocks?snapshot_id=invalid").status_code == 422


def test_exchange_report_scope_and_dividend_columns():
    day = date(2026, 11, 6)
    fields = ["除權息日期", "代號", "權/息", "現金股利"]
    report = {"date": "20261106~20261106", "stat": "ok",
              "tables": [{"fields": fields, "totalCount": 1, "data": [["115/11/06", "2330", "除息", "4.5"]]}]}
    row_day, code, record = site_update.action_rows(json.dumps(report), "TPEx", day, day, *fields[:2])[0]
    assert (row_day, code) == (str(day), "2330")
    assert site_update.normalize_action("ex_right", record, "TPEx")["cash"] == "4.5"
    report["tables"][0]["totalCount"] = 2
    with pytest.raises(ValueError, match="truncated"):
        site_update.action_rows(json.dumps(report), "TPEx", day, day, *fields[:2])


def test_pending_market_does_not_apply_half_day_and_retry_can_resume(monkeypatch):
    previous, target = "2026-11-05", "2026-11-06"
    seed = {"official_through": previous, "official_dates": {market: previous for market in site_update.MARKETS},
            "calendar": {market: [previous] for market in site_update.MARKETS},
            "stocks": [stock({previous: row("100", 0, 20, 0, 0, 20)})]}
    monkeypatch.setattr(site_update, "trading_days", lambda *args: {target})
    monkeypatch.setattr(site_update, "fetch_actions", lambda *args: ({}, {"source": "abc"}))
    def reports(client, market, day):
        if market == "TPEx":
            raise site_update.NotPublished("pending")
        return {"2330": {"close": D(95)}}, {"2330": {"balance": 20}}
    monkeypatch.setattr(site_update, "daily_reports", reports)
    result = site_update.refresh(seed, date.fromisoformat(target), None)
    assert result["pending"] == target
    assert target not in seed["stocks"][0]["rows"]
    assert seed["official_through"] == previous
    monkeypatch.setattr(site_update, "daily_reports", lambda *args: ({"2330": {"close": D(95)}}, {"2330": {"balance": 20}}))
    assert site_update.refresh(seed, date.fromisoformat(target), None)["through"] == target
    once = json.dumps(seed, default=str, sort_keys=True)
    site_update.refresh(seed, date.fromisoformat(target), None)
    assert json.dumps(seed, default=str, sort_keys=True) == once
