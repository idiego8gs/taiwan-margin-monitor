import json
from datetime import date

import pytest

from app import market_breadth as mb

DAY = date(2026, 11, 20)
TWSE = {"stat": "OK", "tables": [{"title": "大盤統計資訊", "fields": ["成交統計"], "data": []},
        {"title": "漲跌證券數合計", "fields": ["類型", "整體市場", "股票"],
         "data": [["上漲(漲停)", "1,354(49)", "16(4)"], ["下跌(跌停)", "9,121(695)", "1,058(179)"],
                  ["持平", "145", "11"], ["未成交", "29,166", "1"], ["無比價", "2,745", "1"]]}]}
TPEX = {"stat": "ok", "date": "20261120", "tables": [{"fields": [
    "上櫃家數", "總資本額(佰萬元)", "總市值(佰萬元)", "本日總成交值(佰萬元)", "本日總成交股數(張數)", "收市指數", "指數漲跌",
    "上漲家數", "漲停家數", "下跌家數", "跌停家數", "平盤家數", "未成交(含暫停交易)家數"],
    "data": [["898", "846,606", "7,375,824", "86,173", "739,064", "263.31", "-22.02", "49", "6", "815", "186", "19", "15"]]}]}


def flow(minutes=271):
    data = []
    for i in range(minutes):
        for second in (0, 5):
            h, m = divmod(9 * 60 + i, 60)
            data.append([f"{h:02d}:{m:02d}:{second:02d}", "1", f"{1000 + i}", "1", f"{2000 + i}", "1", f"{10 * i}", f"{100 * i}"])
    return {"stat": "OK", "fields": mb.FLOW_FIELDS, "data": data}


def test_official_breadth_of_2026_11_20():
    assert mb.parse_twse_breadth(json.dumps(TWSE), DAY) == {"up": 16, "limit_up": 4, "down": 1058, "limit_down": 179, "flat": 11}
    assert mb.parse_tpex_breadth(json.dumps(TPEX), DAY) == {"up": 49, "limit_up": 6, "down": 815, "limit_down": 186, "flat": 19}
    assert mb.parse_twse_breadth(json.dumps({"stat": "很抱歉，沒有符合條件的資料!"}), DAY) is None
    with pytest.raises(ValueError):
        mb.parse_tpex_breadth(json.dumps({**TPEX, "date": "20261119"}), DAY)


def test_flow_is_sampled_per_minute():
    curve = mb.parse_twse_flow(json.dumps(flow()))
    assert len(curve) == 271 and curve[0] == [0, 1000, 2000, 0, 0] and curve[-1] == [270, 1270, 2270, 2700, 27000]
    with pytest.raises(ValueError):
        mb.parse_twse_flow(json.dumps(flow(100)))


def test_refresh_backfills_and_baseline(tmp_path):
    class Client:
        def get(self, url, market):
            if "MI_INDEX" in url:
                return json.dumps(TWSE)
            if "highlight" in url:
                return json.dumps({**TPEX, "date": url.split("date=")[1][:10].replace("/", "")})
            return json.dumps(flow())
    days = ["2026-11-18", "2026-11-19", "2026-11-20"]
    seed = {"calendar": {"TWSE": days}, "official_through": days[-1]}
    assert mb.refresh(seed, Client())["breadth_added"] == 3
    assert mb.refresh(seed, Client())["breadth_added"] == 0
    base = mb.baseline(seed["intraday_curves"])
    assert base["days"] == days and base["value"][-1] == 27000 and base["latest"]["date"] == days[-1]


def test_live_signals_follow_the_rearm_rule():
    rows = [["2026-11-13", 151.0], ["2026-11-16", 139.9], ["2026-11-17", 150.5], ["2026-11-18", 137.6],
            ["2026-11-19", 129.2], ["2026-11-20", 120.3]]
    taiex = {"2026-11-19": "100", "2026-11-20": "90"}
    live = mb.live_signals(rows, taiex)
    found = {(e["threshold"], e["signal"]) for e in live["events"]}
    # 11/16 belongs to the published backtest; 11/18 is a new 140% signal after re-arming at 150.5.
    assert (140, "2026-11-18") in found and (130, "2026-11-19") in found and (140, "2026-11-16") not in found
    signal = next(e for e in live["events"] if e["threshold"] == 130)
    assert signal["entry"] == "2026-11-20" and signal["r20"] is None
