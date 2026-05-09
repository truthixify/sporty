from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from src.api.deps import SessionDep
from src.db import queries
from src.db.models import Event


router = APIRouter(tags=["Events"])


_FOOTBALL_FIELDS = (
    "match_day", "phase", "leg_order",
    "home_team_id", "away_team_id", "home_score", "away_score",
)
_RACE_FIELDS = (
    "num_runners", "winner_id", "second_id", "third_id",
    "final_order", "surface", "distance", "weather",
)


def _summarize_event(e: Event) -> dict[str, Any]:
    """Compact, product-aware projection. Football events get the football
    fields; race events get the race fields. We always include
    `server_status` so consumers can tell SCHEDULED rows from FINISHED ones."""
    base: dict[str, Any] = {
        "e_block_id": e.e_block_id,
        "schema_id": e.schema_id,
        "season_id": e.season_id,
        "product": e.product,
        "server_status": e.server_status,
        "event_time": e.event_time,
        "captured_ts": e.captured_ts,
        "settled_ts": e.settled_ts,
    }
    if e.product == "football":
        for f in _FOOTBALL_FIELDS:
            base[f] = getattr(e, f)
    else:
        for f in _RACE_FIELDS:
            base[f] = getattr(e, f)
    return base


@router.get(
    "/events/recent",
    summary="N most recent events",
    description=(
        "Returns up to `limit` events ordered by `captured_ts` descending "
        "across all products. Each row is a compact, product-aware summary: "
        "football rows include `home_*`/`away_*`/`match_day`/`phase`; race "
        "rows include `num_runners`/`winner_id`/`final_order`/`surface`/"
        "`distance`. Look at `server_status` to tell whether scores/winners "
        "are missing because the match is `SCHEDULED` (no result yet) or "
        "because we never captured a result frame for it."
    ),
)
def events_recent(session: SessionDep, limit: int = Query(50, ge=1, le=500)) -> dict:
    rows = session.scalars(
        select(Event).order_by(Event.captured_ts.desc()).limit(limit)
    ).all()
    return {"count": len(rows), "events": [_summarize_event(e) for e in rows]}


@router.get(
    "/events",
    summary="Filtered event list",
    description=(
        "Filter events by any combination of `product`, `schema_id`, "
        "`server_status`, and `season_id`. Ordered by `event_time` "
        "descending. Returns the same compact, product-aware summary as "
        "`/events/recent`."
    ),
)
def events_filter(
    session: SessionDep,
    product: Optional[str] = Query(
        None,
        description="Filter by product (football, dogs, horses, speedway, motorbikes, mma).",
    ),
    schema_id: Optional[int] = Query(None, description="Filter to one schema (= one league or race series)."),
    server_status: Optional[str] = Query(
        None,
        description="Filter by `SCHEDULED`, `STARTED`, `FINISHED`, etc.",
    ),
    season_id: Optional[int] = Query(None, description="Filter to one season (only meaningful for football)."),
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
    return {"count": len(rows), "events": [_summarize_event(e) for e in rows]}


@router.get(
    "/events/{e_block_id}",
    summary="One event with everything",
    description=(
        "Full event payload: the row itself plus joined participants "
        "(football), runners (races), all `odds` slots, and standings "
        "snapshot. `won_markets` is split into a list. Returns 404 if the "
        "id is unknown."
    ),
    responses={404: {"description": "No event with that e_block_id."}},
)
def event_detail(session: SessionDep, e_block_id: int) -> dict:
    out = queries.event_detail(session, e_block_id)
    if out is None:
        raise HTTPException(status_code=404, detail=f"event {e_block_id} not found")
    return out
