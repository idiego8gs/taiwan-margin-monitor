"""Build and serve the immutable data contract shared by FastAPI and Pages."""
import argparse
import gzip
import json
import os
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import warehouse_schema as tables
from .calculation import DIVIDEND, DIVIDEND_RESULT, REDUCTION, SPLIT, Event, load_actions
from .db import ProviderResponse, engine
from .engine import Action, DataQualityError
from .factor_data import segmented_series

MODEL = "site-close-principal-0.6-v1"
START = date(2021, 10, 1)
MARKETS = ("TWSE", "TPEx")
ACTION_DATASETS = (DIVIDEND, DIVIDEND_RESULT, SPLIT, REDUCTION)
PRICE_FIELDS = ("open", "high", "low", "close", "volume", "turnover")
MARGIN_FIELDS = ("previous_balance", "margin_buy", "margin_sell", "repayment", "balance", "limit")
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / ".local" / "site"


def json_bytes(value):
    return json.dumps(value, default=str, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def read_seed(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        seed = json.load(stream)
    if seed["format"] != 1 or len(seed["stocks"]) != 500:
        raise ValueError("invalid site seed format or universe")
    return seed


def write_seed(path, seed):
    atomic_write(path, gzip.compress(json_bytes(seed), mtime=0))


def selected_rows(connection, table, security_id, market, through):
    # Match the established research preference, with deterministic tie-breaking.
    query = (select(table).where(table.c.security_id == security_id,
             table.c.market == market, table.c.is_current.is_(True),
             table.c.trading_date.between(START, through))
             .order_by(table.c.trading_date, table.c.observed_at, table.c.id))
    result = {}
    for row in connection.execute(query).mappings():
        field = "close" if table is tables.prices else "balance"
        if row[field] is None:
            continue
        if row["trading_date"] not in result or row["provider"] == "FinMind":
            result[row["trading_date"]] = row
    return result


def action_record(event):
    return {
        "kind": event.kind, "source": event.source, "reason": event.reason,
        "multiplier": str(event.action.share_multiplier) if event.action else None,
        "cash": str(event.action.cash_distribution) if event.action else None,
    }


def historical_actions(connection, security_id, code, first, last):
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        ends = [session.scalar(select(func.max(ProviderResponse.end_date)).where(
            ProviderResponse.provider == "FinMind", ProviderResponse.code == code,
            ProviderResponse.dataset == dataset, ProviderResponse.http_status == 200,
            ProviderResponse.start_date <= first)) for dataset in ACTION_DATASETS]
        covered = min(last, *ends) if all(ends) else None
        try:
            events, _ = load_actions(session, code, first, covered) if covered and covered >= first else ({}, [])
        except DataQualityError:
            # A conflicting archive cannot establish that an event was absent.
            covered, events = None, {}
    normalized = {str(day): action_record(event) for day, event in events.items()}
    normalized_rows = connection.execute(select(tables.actions).where(
        tables.actions.c.security_id == security_id,
        tables.actions.c.effective_date.between(first, last)).order_by(tables.actions.c.id)).mappings()
    for row in normalized_rows:
        day = str(row["effective_date"])
        if row["kind"] not in ("par_value_change", "split", "capital_reduction"):
            continue
        multiplier = row["share_multiplier"]
        valid = row["verified"] and multiplier is not None and multiplier > 0
        record = {"kind": row["kind"], "source": f"warehouse-action:{row['id']}",
                  "multiplier": str(multiplier) if valid else None, "cash": "0" if valid else None,
                  "reason": None if valid else "公司行動換股條款待核對"}
        prior = normalized.get(day)
        if prior and prior["multiplier"] and prior != record:
            record.update(multiplier=None, cash=None, reason="同日公司行動條款需要合併核對")
        normalized[day] = record
    return normalized, str(covered) if covered else None


def warehouse_seed(target=engine):
    isolation = "REPEATABLE READ" if target.dialect.name == "postgresql" else "SERIALIZABLE"
    with target.connect().execution_options(isolation_level=isolation) as conn:
        universe = conn.execute(select(tables.universes).where(
            tables.universes.c.name == "top500").order_by(
            tables.universes.c.as_of.desc(), tables.universes.c.id).limit(1)).mappings().one()
        stocks = conn.execute(select(tables.securities, tables.members.c.rank).join(
            tables.members, tables.members.c.security_id == tables.securities.c.id).where(
            tables.members.c.universe_id == universe["id"]).order_by(
            tables.members.c.rank, tables.securities.c.id)).mappings().all()
        if len(stocks) != 500:
            raise ValueError(f"expected 500 members, found {len(stocks)}")
        through = conn.scalar(select(func.max(tables.prices.c.trading_date)))
        calendar = {market: sorted({str(day) for day in conn.scalars(select(
            tables.calendar.c.trading_date).where(tables.calendar.c.market == market,
            tables.calendar.c.is_open.is_(True), tables.calendar.c.trading_date.between(START, through)))})
            for market in MARKETS}
        result = []
        for stock in stocks:
            sid = stock["id"]
            listings = conn.execute(select(tables.listings).where(
                tables.listings.c.security_id == sid)).mappings().all()
            market = listings[0]["market"] if len(listings) == 1 else None
            prices = selected_rows(conn, tables.prices, sid, market, through) if market else {}
            margins = selected_rows(conn, tables.margins, sid, market, through) if market else {}
            days = sorted(prices.keys() | margins.keys())
            events, covered = historical_actions(conn, sid, stock["code"], days[0], days[-1]) if days else ({}, None)
            rows = {}
            for day in days:
                p, m = prices.get(day, {}), margins.get(day, {})
                rows[str(day)] = {
                    **{key: p.get(key) for key in PRICE_FIELDS[:4]},
                    **{key: m.get(key) for key in MARGIN_FIELDS[:-1]},
                    "volume": p.get("volume_shares"), "turnover": p.get("turnover_ntd"),
                    "limit": m.get("margin_limit"),
                    "price_source": p.get("provider"), "margin_source": m.get("provider"),
                    "price_revision": p.get("value_sha256"), "margin_revision": m.get("value_sha256"),
                }
            result.append({"security_id": sid, "code": stock["code"], "name": stock["name"],
                           "rank": stock["rank"], "market": market, "rows": rows, "events": events,
                           "action_through": covered})
    return {"format": 1, "universe_date": str(universe["as_of"]), "universe_id": universe["id"],
            "calendar": calendar, "stocks": result, "official_through": None,
            "official_dates": {market: None for market in MARKETS}}


def decimal_or_none(value):
    return Decimal(str(value)) if value is not None else None


def estimate_history(stock, market_days):
    calendar = {date.fromisoformat(day) for day in market_days}
    raw = stock["rows"]
    if not raw:
        return []
    first, last = min(raw), max(raw)
    rows = []
    events = {}
    for key in sorted(set(raw) | {day for day in market_days if first <= day <= last}):
        observation = raw.get(key, {})
        day = date.fromisoformat(key)
        rows.append(SimpleNamespace(trading_date=day,
                    **{field: observation.get(field) for field in MARGIN_FIELDS},
                    **{field: decimal_or_none(observation.get(field)) for field in PRICE_FIELDS[:4]},
                    volume=observation.get("volume"), turnover=decimal_or_none(observation.get("turnover"))))
        event = stock["events"].get(key)
        if event and event["kind"] != "cash_dividend":
            action = (Action(share_multiplier=Decimal(event["multiplier"]))
                      if event["multiplier"] is not None else None)
            events[day] = Event(day, action, event["kind"], event["source"], event["reason"])
        elif not event and (not stock.get("action_through") or key > stock["action_through"]):
            if key not in stock.get("checked_action_days", ()):
                events[day] = Event(day, None, "corporate_action_coverage_gap", "site",
                                    "公司行動尚未核驗")
    calculated, breaks = segmented_series(rows, events, calendar, keep_cash_out_of_principal=True)
    values = {str(item["date"]): item for item in calculated}
    reasons = {str(item["date"]): item["kind"] for item in breaks}
    result = []
    for row in rows:
        key = str(row.trading_date)
        inp = raw.get(key, {})
        value = values.get(key, {})
        quality = reasons.get(key)
        if not quality:
            if row.balance == 0 and value:
                quality = "not_applicable"
            elif value.get("raw_maintenance") is None:
                quality = "missing_price" if row.close is None else "insufficient_data"
            else:
                quality = "warmup" if value["warmup"] else "estimated"
        result.append({"date": key, **{field: inp.get(field) for field in PRICE_FIELDS[:5]},
                       "balance": row.balance, "cost": value.get("estimated_cost"),
                       "maintenance": value.get("raw_maintenance"),
                       "reference_130": value.get("reference_price_uncalibrated"),
                       "segment": value.get("segment"), "warmup": bool(value.get("warmup")),
                       "quality": quality, "price_source": inp.get("price_source"),
                       "margin_source": inp.get("margin_source"), "action": stock["events"].get(key)})
    return result


def render(seed, root):
    source_hash = sha256(json_bytes(seed)).hexdigest()
    version = sha256(json_bytes([MODEL, source_hash])).hexdigest()
    directory = root / "snapshots" / version
    manifest_path = directory / "index.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_bytes())
    else:
        manifest = {"snapshot_id": version, "model": MODEL, "source_hash": source_hash,
                    "universe_date": seed["universe_date"], "finance_ratio": "0.6",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "official_through": seed["official_through"], "official_dates": seed["official_dates"],
                    "workflow_run": seed.get("workflow_run"), "stocks": []}
        if "taiex_close" in seed:
            manifest["taiex_close"] = seed["taiex_close"]
        for stock in seed["stocks"]:
            meta = {field: stock[field] for field in ("security_id", "code", "name", "market", "rank")}
            rows = estimate_history(stock, seed["calendar"].get(stock["market"], []))
            quote = next((row for row in reversed(rows) if row["close"] is not None), None)
            estimate = next((row for row in reversed(rows) if row["maintenance"] is not None), None)
            latest = rows[-1] if rows else None
            meta.update(latest_quote_date=quote["date"] if quote else None,
                        latest_estimate_date=estimate["date"] if estimate else None,
                        latest=latest)
            payload = json_bytes({"snapshot_id": version, "model": MODEL, "stock": meta, "items": rows})
            atomic_write(directory / "stocks" / f"{stock['security_id']}.json", payload)
            manifest["stocks"].append({**meta, "history_sha256": sha256(payload).hexdigest()})
        manifest["review_count"] = sum(
            not row["latest"] or row["latest"]["quality"] not in ("estimated", "warmup", "not_applicable")
            for row in manifest["stocks"])
        payload = json_bytes(manifest)
        atomic_write(manifest_path, payload)
        manifest = json.loads(payload)
    atomic_write(root / "latest.json", json_bytes({"snapshot_id": version}))
    return manifest


def snapshot_dir(root, snapshot_id=None):
    key = snapshot_id or json.loads((root / "latest.json").read_bytes())["snapshot_id"]
    if not re.fullmatch(r"[a-f0-9]{64}", key):
        raise ValueError("invalid snapshot ID")
    return root / "snapshots" / key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--from-warehouse", action="store_true")
    args = parser.parse_args()
    if args.from_warehouse:
        write_seed(args.seed, warehouse_seed())
    result = render(read_seed(args.seed), args.root)
    print(json_bytes({key: value for key, value in result.items() if key != "stocks"}).decode())


if __name__ == "__main__":
    main()
