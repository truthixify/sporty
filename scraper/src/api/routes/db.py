from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from src.api.deps import SessionDep
from src.db.models import (
    AlertLog,
    CaptureSession,
    Event,
    EventParticipantFootball,
    EventRunner,
    MatchDaySnapshot,
    MetricsSnapshot,
    Odds,
    OddsHistory,
    ParseWatermark,
    Schema,
    SchemaMarket,
    SchemaParticipant,
    Season,
    Standing,
)


router = APIRouter()


_TABLES = [
    Schema, SchemaMarket, SchemaParticipant, Season, Event, Odds, OddsHistory,
    EventParticipantFootball, EventRunner, Standing, MatchDaySnapshot,
    ParseWatermark, CaptureSession, MetricsSnapshot, AlertLog,
]


@router.get("/db/stats")
def db_stats(session: SessionDep) -> dict:
    rows = {}
    for model in _TABLES:
        count = session.scalar(select(func.count()).select_from(model)) or 0
        rows[model.__tablename__] = int(count)
    return {"row_counts": rows}
