from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from src.api.deps import SessionDep
from src.db import queries
from src.db.models import Event


router = APIRouter()


@router.get("/events/recent")
def events_recent(session: SessionDep, limit: int = Query(50, ge=1, le=500)) -> dict:
    rows = session.scalars(
        select(Event).order_by(Event.captured_ts.desc()).limit(limit)
    ).all()
    return {"count": len(rows), "events": [
        {
            "e_block_id": e.e_block_id, "schema_id": e.schema_id,
            "product": e.product, "event_time": e.event_time,
            "home_score": e.home_score, "away_score": e.away_score,
            "winner_id": e.winner_id, "captured_ts": e.captured_ts,
        }
        for e in rows
    ]}


@router.get("/events")
def events_filter(
    session: SessionDep,
    product: Optional[str] = None,
    schema_id: Optional[int] = None,
    server_status: Optional[str] = None,
    season_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    stmt = select(Event)
    if product is not None:
        stmt = stmt.where(Event.product == product)
    if schema_id is not None:
        stmt = stmt.where(Event.schema_id == schema_id)
    if server_status is not None:
        stmt = stmt.where(Event.server_status == server_status)
    if season_id is not None:
        stmt = stmt.where(Event.season_id == season_id)
    stmt = stmt.order_by(Event.event_time.desc()).limit(limit)
    rows = session.scalars(stmt).all()
    return {"count": len(rows), "events": [
        {"e_block_id": e.e_block_id, "schema_id": e.schema_id, "product": e.product,
         "event_time": e.event_time, "match_day": e.match_day,
         "home_score": e.home_score, "away_score": e.away_score}
        for e in rows
    ]}


@router.get("/events/{e_block_id}")
def event_detail(session: SessionDep, e_block_id: int) -> dict:
    out = queries.event_detail(session, e_block_id)
    if out is None:
        raise HTTPException(status_code=404, detail=f"event {e_block_id} not found")
    return out
