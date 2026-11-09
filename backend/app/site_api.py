"""Read the exact snapshot files used in the static deployment."""
import json
import os
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

from .site_snapshot import DEFAULT_ROOT, snapshot_dir

router = APIRouter(prefix="/api/v2/stocks")


def locate(snapshot_id):
    root = Path(os.getenv("MARGIN_SITE_ROOT", str(DEFAULT_ROOT)))
    try:
        return snapshot_dir(root, snapshot_id)
    except FileNotFoundError as exc:
        raise HTTPException(503, "網站資料快照準備中") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def read(path):
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise HTTPException(404, "找不到指定快照或證券") from exc


@router.get("")
def stocks(snapshot_id: str | None = None):
    return Response(read(locate(snapshot_id) / "index.json"), media_type="application/json")


@router.get("/{security_id}/history")
def history(security_id: int, snapshot_id: str | None = None,
            start: date | None = None, end: date | None = None):
    if start and end and start > end:
        raise HTTPException(422, "起始日期須早於結束日期")
    payload = read(locate(snapshot_id) / "stocks" / f"{security_id}.json")
    if not start and not end:
        return Response(payload, media_type="application/json")
    data = json.loads(payload)
    data["items"] = [row for row in data["items"]
                     if (not start or row["date"] >= str(start)) and (not end or row["date"] <= str(end))]
    return data
