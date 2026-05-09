from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from src.api.deps import ConfigDep, SessionDep
from src.db.models import Event


router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    summary="Liveness probe",
    description=(
        "Returns 200 with `{\"status\": \"ok\"}` as long as the API process "
        "is responsive. Does not touch the database. Suitable for Kubernetes "
        "liveness checks or a basic uptime monitor."
    ),
)
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/ready",
    summary="Readiness probe",
    description=(
        "Returns 200 if the database is reachable AND we've seen at least one "
        "event in the last hour. The `ok` flag in the body tells you whether "
        "we'd consider the system warm; 503 means the DB itself failed. "
        "`last_event_age_s` is the age of the freshest event in seconds."
    ),
    responses={503: {"description": "Database unreachable."}},
)
def ready(session: SessionDep, cfg: ConfigDep) -> dict:
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
