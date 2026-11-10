"""Fail closed if a checkpoint or Pages artifact contains unexpected data."""
import argparse
import gzip
import json
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
    "official_dates", "action_evidence", "workflow_run", "taiex_close",
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
