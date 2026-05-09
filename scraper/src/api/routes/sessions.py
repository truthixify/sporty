from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from src.api.deps import SessionDep
from src.db.models import CaptureSession


router = APIRouter()


@router.get("/sessions")
def list_sessions(session: SessionDep) -> dict:
    rows = session.scalars(
        select(CaptureSession).order_by(CaptureSession.started_ts.desc()).limit(100)
    ).all()
    return {"count": len(rows), "sessions": [
        {
            "session_id": r.session_id,
            "started_ts": r.started_ts,
            "ended_ts": r.ended_ts,
            "end_reason": r.end_reason,
            "frames_in": r.frames_in,
            "frames_out": r.frames_out,
            "error_count": r.error_count,
        }
        for r in rows
    ]}


@router.get("/sessions/{session_id}")
def session_detail(session: SessionDep, session_id: str) -> dict:
    row = session.get(CaptureSession, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "session_id": row.session_id,
        "started_ts": row.started_ts,
        "ended_ts": row.ended_ts,
        "end_reason": row.end_reason,
        "frames_in": row.frames_in,
        "frames_out": row.frames_out,
        "error_count": row.error_count,
        "notes": row.notes,
    }
