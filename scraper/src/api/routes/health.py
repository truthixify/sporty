from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from src.api.deps import ConfigDep, SessionDep
from src.db.models import Event


router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(session: SessionDep, cfg: ConfigDep) -> dict:
    """Readiness: DB reachable AND we've captured an event in the last hour."""
    try:
        last_ts = session.scalar(select(func.max(Event.captured_ts)))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"db unreachable: {exc}") from exc
    age = None if last_ts is None else max(0.0, time.time() - float(last_ts))
    fresh = age is not None and age < 3600.0
    return {
        "ok": fresh,
        "last_event_age_s": age,
        "captures_dir": str(cfg.paths.captures_dir),
    }
