"""Fail closed if a checkpoint or Pages artifact contains unexpected data."""
import argparse
import gzip
import json
import math
import re
from pathlib import Path


SENSITIVE = re.compile(
    rb"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"
    rb"|(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"
    rb"|(?:postgres(?:ql)?|mysql)(?:\+[a-z0-9_]+)?://[^\s:@/]+:[^\s@/]+@"
    rb"|[A-Za-z]:[\\/]{1,2}(?:Users|Documents and Settings)[\\/]{1,2}"
    rb"|/(?:home/runner|Users/[^/]+)/"
)
ROOT_FILES = {
    ".nojekyll", "404.html", "index.html", "index.txt", "research.html",
    "research-results-4bdea2ad5b22.txt", "database.html", "database-status.json",
    "market/index.html", "market/index.txt", "data/wantgoo-market.json",
}
ROW_FIELDS = {
    "open", "high", "low", "close", "volume", "turnover", "previous_balance",
    "margin_buy", "margin_sell", "repayment", "balance", "limit", "price_source",
    "margin_source", "price_revision", "margin_revision",
}
STOCK_FIELDS = {
    "security_id", "code", "name", "rank", "market", "rows", "events",
    "action_through", "checked_action_days",
}
EVENT_FIELDS = {"kind", "source", "reason", "multiplier", "cash"}
SEED_FIELDS = {
    "format", "universe_date", "universe_id", "calendar", "stocks", "official_through",
    "official_dates", "action_evidence", "workflow_run", "taiex_close", "wantgoo_daily",
}
MARKETS = {"TWSE", "TPEx"}
HASH = re.compile(r"[a-f0-9]{64}\Z")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


def scan(content, key):
    if SENSITIVE.search(content) or (key and key in content):
        raise ValueError("Sensitive material detected in export")


def fields(obj, allowed):
    if not isinstance(obj, dict) or obj.keys() - allowed:
        raise ValueError("Unexpected checkpoint fields")


def dated_records(records, allowed):
    for day, record in records.items():
        if not DATE.fullmatch(day):
            raise ValueError("Invalid checkpoint record date")
        fields(record, allowed)
        if any(type(value) not in (str, int, float, type(None)) for value in record.values()):
            raise ValueError("Nested checkpoint record data")


def validate_market_state(seed):
    state = seed['wantgoo_daily']
    fields(state, {'format', 'baseVersion', 'through', 'positions', 'rows', 'references'})
    if state['format'] != 1 or not HASH.fullmatch(state['baseVersion']):
        raise ValueError('Invalid market cost state version')
    codes = {s['code'] for s in seed['stocks'] if s['market'] == 'TWSE'}
    if set(state['positions']) != codes:
        raise ValueError('Market cost state stock scope changed')
    def finite(value):
        return type(value) in (float, int) and math.isfinite(value)
    for code, (cost, weight, lots) in state['positions'].items():
        if not re.fullmatch(r'[1-9][0-9]{3}', code) or (lots is not None and (type(lots) is not int or lots < 0)):
            raise ValueError('Invalid market position')
        if (cost is None and weight is not None) or (cost is not None and (
                not finite(cost) or cost <= 0 or not finite(weight) or not 0 <= weight <= 1 or not lots)):
            raise ValueError('Invalid market position cost')
    start = '2026-11-09'
    last = start
    dates = set()
    for day, ratio, original, included, positive, coverage, market_coverage, weight, missing, value, debt in state['rows']:
        if (not DATE.fullmatch(day) or day <= last or original is not None or
                any(type(n) is not int or n < 0 for n in (included, positive, missing, value, debt)) or
                not 0 < included <= positive <= len(codes) or not debt or not value or
                any(not finite(n) or not 0 <= n <= 1 for n in (coverage, weight)) or
                (market_coverage is not None and (not finite(market_coverage) or not 0 < market_coverage <= 1)) or
                not finite(ratio) or not math.isclose(ratio, 100 * value / debt, abs_tol=.0001)):
            raise ValueError('Invalid daily market aggregate')
        dates.add(day)
        last = day
    if last != state['through'] or (last != start and last > (seed['official_dates']['TWSE'] or '')):
        raise ValueError('Market cursor exceeds verified dates')
    if any(day not in dates or not finite(ratio) or not 0 < ratio < 1000 for day, ratio in state['references'].items()):
        raise ValueError('Invalid market reference dates')


def check_seed(path, key):
    with gzip.open(path, "rb") as stream:
        content = stream.read()
    scan(content, key)
    seed = json.loads(content)
    del content
    fields(seed, SEED_FIELDS)
    if seed["format"] != 1 or len({stock["security_id"] for stock in seed["stocks"]}) != 500 or len(seed["stocks"]) != 500:
        raise ValueError("Invalid checkpoint universe")
    for stock in seed["stocks"]:
        fields(stock, STOCK_FIELDS)
        dated_records(stock["rows"], ROW_FIELDS)
        dated_records(stock["events"], EVENT_FIELDS)
    if 'wantgoo_daily' in seed:
        validate_market_state(seed)
    for day, close in seed.get("taiex_close", {}).items():
        if (not DATE.fullmatch(day) or day > seed["official_through"] or
                not isinstance(close, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", close) or
                float(close) <= 0):
            raise ValueError("Invalid TAIEX closing observation")
    for market, sources in seed.get("action_evidence", {}).items():
        if market not in MARKETS or any(
            not url.startswith(("https://www.twse.com.tw/", "https://www.tpex.org.tw/"))
            or not HASH.fullmatch(digest)
            for url, digest in sources.items()
        ):
            raise ValueError("Unexpected checkpoint evidence")
    print("Checkpoint fields and sensitive material scan passed for 500 stocks.")


def check_pages(root, key):
    version = json.loads((root / "data/latest.json").read_bytes())["snapshot_id"]
    if not HASH.fullmatch(version):
        raise ValueError("Invalid Pages snapshot ID")
    folder = f"data/snapshots/{version}"
    index = json.loads((root / folder / "index.json").read_bytes())
    ids = {str(stock["security_id"]) for stock in index["stocks"]}
    if len(ids) != 500 or any(not sid.isdecimal() for sid in ids):
        raise ValueError("Invalid Pages stock IDs")
    data_files = {"data/latest.json", f"{folder}/index.json"} | {
        f"{folder}/stocks/{sid}.json" for sid in ids
    }
    files = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or path.is_junction():
            raise ValueError("Links are forbidden in the Pages artifact")
        if path.is_dir():
            continue
        if not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError("Only regular files are allowed in the Pages artifact")
        if relative not in ROOT_FILES | data_files and not (
            relative.startswith(("_next/", "stocks/")) and
            path.suffix in {".js", ".css", ".html", ".txt", ".woff", ".woff2"} and
            not any(part.startswith(".") for part in path.relative_to(root).parts)
        ):
            raise ValueError("Unexpected Pages artifact path or file type")
        scan(path.read_bytes(), key)
        files.add(relative)
    if not (data_files | ROOT_FILES) <= files:
        raise ValueError("Pages artifact is incomplete")
    print(f"Pages allowlist and sensitive material scan passed for {len(files)} files.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("seed", "pages"))
    parser.add_argument("path", type=Path)
    parser.add_argument("--key-file", type=Path, help="Optional local check for the exact key bytes")
    args = parser.parse_args()
    key = args.key_file.read_bytes().strip() if args.key_file else None
    (check_seed if args.kind == "seed" else check_pages)(args.path, key)
