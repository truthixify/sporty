from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import func, select

from src.api.deps import ConfigDep, SessionDep
from src.db.models import Event, ParseWatermark


router = APIRouter(tags=["Health"])


@router.get(
    "/metrics",
    summary="All metrics in one payload",
    description=(
        "Returns capture and parser metrics together. Convenience wrapper "
        "around `/metrics/capture` + `/metrics/parse`."
    ),
)
def metrics(session: SessionDep, cfg: ConfigDep) -> dict:
    return {
        "capture": _capture_metrics(session, cfg),
        "parse": _parse_metrics(session),
    }


@router.get(
    "/metrics/capture",
    summary="Capture rate metrics",
    description=(
        "Capture-side rates the watchdog uses to decide when to alert: "
        "`events_per_hour` (DB-derived) and `frames_per_min` (sampled from "
        "the latest journal file's tail). `last_event_age_s` tells you how "
        "long since the freshest event landed; sustained age over the "
        "`stale_data_seconds` threshold fires a critical alert."
    ),
)
def metrics_capture(session: SessionDep, cfg: ConfigDep) -> dict:
    return _capture_metrics(session, cfg)


@router.get(
    "/metrics/parse",
    summary="Parser watermark snapshot",
    description=(
        "One row per journal file the parser has seen. `last_offset` is the "
        "byte offset where the next incremental parse will resume; "
        "`parsed_count` is the number of lines processed in the most recent "
        "run. `last_run_ts` is the unix timestamp of the last parse."
    ),
)
def metrics_parse(session: SessionDep) -> dict:
    return _parse_metrics(session)


def _capture_metrics(session, cfg) -> dict:
    now = time.time()
    last_event_ts = session.scalar(select(func.max(Event.captured_ts)))
    events_last_hour = session.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.captured_ts >= now - 3600.0)
    ) or 0
    frames_per_min = _journal_frames_per_min(cfg.paths.captures_dir, now=now)
    return {
        "last_event_ts": float(last_event_ts) if last_event_ts is not None else None,
        "last_event_age_s": (now - float(last_event_ts)) if last_event_ts is not None else None,
        "events_per_hour": float(events_last_hour),
        "frames_per_min": frames_per_min,
    }


def _parse_metrics(session) -> dict:
    rows = session.scalars(select(ParseWatermark)).all()
    return {
        "files_tracked": len(rows),
        "watermarks": [
            {
                "journal_file": r.journal_file,
                "last_offset": r.last_offset,
                "last_line": r.last_line,
                "parsed_count": r.parsed_count,
                "last_run_ts": r.last_run_ts,
            }
            for r in rows
        ],
    }


def _journal_frames_per_min(captures_dir: Path, *, now: float) -> float:
    """Crude estimate: count lines added in the last 60s of the most-recent
    journal. We only sample the tail to keep this cheap."""
    if not captures_dir.exists():
        return 0.0
    files = sorted(captures_dir.glob("vs_*.jsonl"))
    if not files:
        return 0.0
    latest = files[-1]
    try:
        st = latest.stat()
    except OSError:
        return 0.0
    if now - st.st_mtime > 120.0:
        return 0.0
    try:
        with latest.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            tail = f.read()
    except OSError:
        return 0.0
    lines = tail.split(b"\n")
    return float(min(len(lines) - 1, 60_000)) / max(1.0, (now - st.st_mtime + 1.0))
