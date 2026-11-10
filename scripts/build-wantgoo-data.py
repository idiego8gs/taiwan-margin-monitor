"""Convert the verified research exports into the site's public market dataset."""
import argparse
import csv
import json
from datetime import date
from math import isfinite
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
    parser.add_argument("--rebuild", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("public/data/wantgoo-market.json"))
    args = parser.parse_args()
    source = args.source
    originals = sorted_days(read_csv(source, "wantgoo_published_exclude_etf_daily.csv"))
    reconstruction = json.loads(args.rebuild.read_text(encoding="utf-8"))
    rebuilt = sorted_days(reconstruction["rows"])
    rebuilt_by_day = {row["date"]: row for row in rebuilt}
    original_by_day = {row["date"]: row for row in originals}
    rows = []
    for day in sorted(original_by_day.keys() | rebuilt_by_day.keys()):
        original = original_by_day.get(day)
        estimate = rebuilt_by_day.get(day, {})
        rows.append([day, estimate.get("estimatePct"),
                     number(original["wantgoo_published_ratio_pct"]) if original else None,
                     estimate.get("included"), estimate.get("positive"), estimate.get("dbCoverage"),
                     estimate.get("marketCoverage"), estimate.get("seedWeight"), estimate.get("missingMargin"),
                     estimate.get("collateralNtd"), estimate.get("debtNtd")])

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
        "version": 2,
        "rebuild": reconstruction["summary"],
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
    print(f"{len(rebuilt)} rebuilt market dates, {len(originals)} source observations and {len(material)} material stock days")


if __name__ == "__main__":
    main()
