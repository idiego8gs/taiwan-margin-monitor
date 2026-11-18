import json
from datetime import date
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app import site_update
from app.benchmark import FIELDS
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


def test_small_balance_revision_keeps_cost_segment():
    days = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
    rows = {days[0]: row("100", 0, 1000, 0, 0, 1000),
            days[1]: row("100", 999, 0, 0, 0, 999),     # exchange revised yesterday's 1000 down to 999
            days[2]: row("100", 1004, 5, 0, 0, 1009),   # +5 lots revision, within 10 lots
            days[3]: row("100", 1500, 0, 0, 0, 1500)}   # +49% revision is a real discontinuity
    items = estimate_history({**stock(rows), "action_through": days[-1]}, days)
    assert [x["segment"] for x in items[:3]] == [items[0]["segment"]] * 3
    assert items[1]["cost"] == items[2]["cost"] == D(100)
    assert items[3]["segment"] != items[0]["segment"]


def test_large_balance_revisions_v4():
    days = [f"2026-11-{n:02d}" for n in range(2, 9)]
    rows = {days[0]: row("100", 0, 1000, 0, 0, 1000),
            days[1]: row("110", 600, 0, 0, 0, 600),     # 400 lots dropped from the report
            days[2]: row("120", 1000, 0, 0, 0, 1000),   # ...and restored the next day
            days[3]: row("90", 800, 0, 0, 0, 800),      # lasting decrease keeps the average cost
            days[4]: row("90", 870, 0, 0, 0, 870),      # +8.75% revision within the 10% limit
            days[5]: row("90", 870, 0, 0, 0, 870),
            days[6]: row("90", 1500, 0, 0, 0, 1500)}    # +72% is still a discontinuity
    items = estimate_history({**stock(rows), "action_through": days[-1]}, days)
    assert [x["segment"] for x in items[:6]] == [items[0]["segment"]] * 6
    assert {x["cost"] for x in items[:6]} == {D(100)}
    assert items[6]["segment"] != items[0]["segment"]


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


def test_index_carries_prior_maintenance(tmp_path):
    days = [f"2026-11-0{n}" for n in range(2, 9)]
    rows = {day: row(str(100 - 3 * i), 20, 0, 0, 0, 20) for i, day in enumerate(days)}
    rows[days[0]] = row("100", 0, 20, 0, 0, 20)
    seed = {"universe_date": days[0], "official_through": days[-1],
            "official_dates": {"TWSE": days[-1], "TPEx": days[-1]},
            "calendar": {"TWSE": days, "TPEx": []}, "stocks": [{**stock(rows), "action_through": days[-1]}]}
    manifest = render(seed, tmp_path)
    item = manifest["stocks"][0]
    history = json.loads((tmp_path / "snapshots" / manifest["snapshot_id"] / "stocks" / "12.json").read_bytes())["items"]
    assert len(history) == 7 and history[-2]["maintenance"] is not None
    assert item["maintenance_1d_ago"] == history[-2]["maintenance"]
    assert item["maintenance_5d_ago"] == history[-6]["maintenance"]

    short = {**seed, "calendar": {"TWSE": days[:2], "TPEx": []},
             "stocks": [stock({day: rows[day] for day in days[:2]})]}
    manifest = render(short, tmp_path / "short")
    item = manifest["stocks"][0]
    history = json.loads((tmp_path / "short" / "snapshots" / manifest["snapshot_id"] / "stocks" / "12.json").read_bytes())["items"]
    assert item["maintenance_1d_ago"] == history[0]["maintenance"]
    assert item["maintenance_5d_ago"] is None


def test_prior_maintenance_ignores_other_cost_segments(tmp_path):
    days = [f"2026-11-0{n}" for n in range(2, 9)]
    rows = {day: row("100", 20, 0, 0, 0, 20) for day in days}
    rows[days[0]] = row("100", 0, 20, 0, 0, 20)
    rows[days[-1]] = row("100", 60, 0, 0, 0, 60)  # balance break (beyond the revision tolerance) restarts the segment
    seed = {"universe_date": days[0], "official_through": days[-1],
            "official_dates": {"TWSE": days[-1], "TPEx": days[-1]},
            "calendar": {"TWSE": days, "TPEx": []}, "stocks": [{**stock(rows), "action_through": days[-1]}]}
    manifest = render(seed, tmp_path)
    history = json.loads((tmp_path / "snapshots" / manifest["snapshot_id"] / "stocks" / "12.json").read_bytes())["items"]
    assert history[-1]["maintenance"] is not None and history[-1]["segment"] != history[-2]["segment"]
    assert history[-2]["maintenance"] is not None
    assert manifest["stocks"][0]["maintenance_1d_ago"] is None
    assert manifest["stocks"][0]["maintenance_5d_ago"] is None


def test_cached_manifest_without_prior_fields_is_rebuilt(tmp_path):
    days = [f"2026-11-0{n}" for n in range(2, 5)]
    rows = {day: row("100", 20, 0, 0, 0, 20) for day in days}
    rows[days[0]] = row("100", 0, 20, 0, 0, 20)
    seed = {"universe_date": days[0], "official_through": days[-1],
            "official_dates": {"TWSE": days[-1], "TPEx": days[-1]},
            "calendar": {"TWSE": days, "TPEx": []}, "stocks": [{**stock(rows), "action_through": days[-1]}]}
    manifest = render(seed, tmp_path)
    path = tmp_path / "snapshots" / manifest["snapshot_id"] / "index.json"
    stale = json.loads(path.read_bytes())
    for item in stale["stocks"]:
        del item["maintenance_1d_ago"], item["maintenance_5d_ago"]
    path.write_text(json.dumps(stale))
    assert "maintenance_1d_ago" in render(seed, tmp_path)["stocks"][0]


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


def test_taiex_snapshot_backfill_and_revision_exclude_future_dates(tmp_path, monkeypatch):
    a, b, future = "2026-11-02", "2026-11-03", "2026-11-04"
    monkeypatch.setattr(site_update, "START", date.fromisoformat(a))
    seed = {"universe_date": a, "official_through": b, "official_dates": {"TWSE": b},
            "calendar": {}, "stocks": []}

    class OfficialIndex:
        close = "25001"

        def get(self, url, market):
            assert market == "TWSE" and "date=20261101" in url
            return json.dumps({"stat": "OK", "fields": FIELDS,
                               "data": [["115/11/02", "25000", "25000", "25000", "25000"],
                                        ["115/11/03", "25001", "25002", "25001", self.close],
                                        ["115/11/04", "25003", "25003", "25003", "25003"]]})

    client = OfficialIndex()
    site_update.refresh_taiex(seed, client)
    assert render(seed, tmp_path)["taiex_close"] == {a: "25000", b: "25001"}
    client.close = "25002"
    site_update.refresh_taiex(seed, client)
    assert render(seed, tmp_path)["taiex_close"] == {a: "25000", b: "25002"}
    assert future not in seed["taiex_close"]


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
