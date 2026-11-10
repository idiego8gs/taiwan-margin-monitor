"""Convert the verified research exports into the site's public market dataset."""
import argparse
import csv
import json
from datetime import date
from math import isclose, isfinite
from pathlib import Path


def read_csv(root, name):
    with (root / name).open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def number(value):
    result = float(value)
    if not isfinite(result):
        raise ValueError("Non-finite value in research export")
    return result


def sorted_days(rows):
    dates = [row["date"] for row in rows]
    if dates != sorted(set(dates)) or any(date.fromisoformat(day).isoformat() != day for day in dates):
        raise ValueError("Expected unique and sorted trading days")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("public/data/wantgoo-market.json"))
    args = parser.parse_args()
    source = args.source
    originals = sorted_days(read_csv(source, "wantgoo_published_exclude_etf_daily.csv"))
    audits = sorted_days(read_csv(source, "market_sample_validation.csv"))
    original_by_day = {row["date"]: row for row in originals}
    audit_by_day = {row["date"]: row for row in audits}
    threshold = 0.85
    for audit in audits:
        published = number(original_by_day[audit["date"]]["wantgoo_published_ratio_pct"])
        if not isclose(published, number(audit["published_ratio_pct"]), abs_tol=1e-9):
            raise ValueError(f"Published ratio mismatch on {audit['date']}")
        if not 0 < number(audit["coverage_of_priced_collateral"]) <= 1:
            raise ValueError(f"Invalid market coverage on {audit['date']}")
    rows = []
    for original in originals:
        day = original["date"]
        sample = audit_by_day.get(day)
        rows.append({
            "date": day,
            "publishedPct": number(original["wantgoo_published_ratio_pct"]),
            "balance100m": number(original["published_margin_balance_thousand_ntd"]) / 100_000,
            "sample": {
                "pct": number(sample["sample_ratio_pct"]),
                "errorPp": number(sample["error_pp"]),
                "coverage": number(sample["coverage_of_priced_collateral"]),
                "included": int(sample["known_stocks"]),
                "total": int(sample["stocks"]),
            } if sample else None,
        })

    paths = read_csv(source, "material_continuous_across_july.csv")
    seed = max(row["seed_date"] for row in paths)
    material = [{
        "date": row["date"],
        "candidatePct": number(row["candidate_ratio_pct"]),
        "publishedPct": number(row["target_ratio_pct"]) if row["target_ratio_pct"] else None,
    } for row in sorted_days([row for row in paths if row["seed_date"] == seed])]
    rollout = json.loads((source / "stock_continuous_rollout_summary.json").read_text(encoding="utf-8"))
    split = read_csv(source, "material_split_audit.csv")
    data = {
        "version": 1,
        "minCoverage": threshold,
        "rows": rows,
        "material": {"seedDate": seed, "rows": material},
        "stockAudit": {
            "checks": int(rollout["checks"]), "stocks": int(rollout["stocks"]),
            "maePp": number(rollout["mae_pp"]), "maxPp": number(rollout["max_abs_error_pp"]),
        },
        "split": {
            "date": split[0]["date"],
            "publishedPct": number(split[0]["published_ratio_pct"]),
            "candidatePct": number(split[0]["candidate_ratio_pct"]),
            "adjustedPct": number(split[1]["candidate_ratio_pct"]),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(rows)} published days, {len(audits)} market checks and {len(material)} material stock days")


if __name__ == "__main__":
    main()
