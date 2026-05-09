from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from src.api.deps import SessionDep
from src.db.models import CaptureSession


router = APIRouter(tags=["Sessions"])


@router.get(
    "/sessions",
    summary="Recent capture sessions",
    description=(
        "The 100 most recent capture-daemon runs, newest first. Each row "
        "captures when the daemon started, when (and why) it stopped, and "
        "how many WebSocket frames it ingested. `end_reason` values like "
        "`recovery_exhausted`, `no_iframe`, or `too_many_recoveries` "
        "correspond to the daemon's exit codes 10/11/12 - the watchdog uses "
        "this table to detect those failure modes."
    ),
)
def list_sessions(session: SessionDep) -> dict:
    rows = session.scalars(
        select(CaptureSession).order_by(CaptureSession.started_ts.desc()).limit(100)
    ).all()
    return {"count": len(rows), "sessions": [_summarize(r) for r in rows]}


@router.get(
    "/sessions/{session_id}",
    summary="One capture session",
    description="Full session detail including operator-set notes.",
    responses={404: {"description": "Session id not found."}},
)
def session_detail(session: SessionDep, session_id: str) -> dict:
    row = session.get(CaptureSession, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {**_summarize(row), "notes": row.notes}


def _summarize(r: CaptureSession) -> dict:
    return {
        "session_id": r.session_id,
        "started_ts": r.started_ts,
        "ended_ts": r.ended_ts,
        "end_reason": r.end_reason,
        "frames_in": r.frames_in,
        "frames_out": r.frames_out,
        "error_count": r.error_count,
    }
