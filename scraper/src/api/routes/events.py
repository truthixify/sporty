from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from src.api.deps import SessionDep
from src.db.models import Event, EventParticipantFootball, EventRunner, Odds, Standing


router = APIRouter()


def _event_payload(session, event: Event) -> dict:
    parts = session.scalars(
        select(EventParticipantFootball).where(EventParticipantFootball.e_block_id == event.e_block_id)
    ).all()
    runners = session.scalars(
        select(EventRunner).where(EventRunner.e_block_id == event.e_block_id)
    ).all()
    odds = session.scalars(
        select(Odds).where(Odds.e_block_id == event.e_block_id).order_by(Odds.slot)
    ).all()
    standings = session.scalars(
        select(Standing).where(Standing.e_block_id == event.e_block_id).order_by(Standing.ranking)
    ).all()
    return {
        "e_block_id": event.e_block_id,
        "schema_id": event.schema_id,
        "season_id": event.season_id,
        "product": event.product,
        "server_status": event.server_status,
        "event_time": event.event_time,
        "captured_ts": event.captured_ts,
        "settled_ts": event.settled_ts,
        "match_day": event.match_day,
        "phase": event.phase,
        "home_team_id": event.home_team_id,
        "away_team_id": event.away_team_id,
        "home_score": event.home_score,
        "away_score": event.away_score,
        "winner_id": event.winner_id,
        "second_id": event.second_id,
        "third_id": event.third_id,
        "num_runners": event.num_runners,
        "won_markets": event.won_markets.split(",") if event.won_markets else [],
        "participants_football": [
            {"side": p.side, "team_id": p.team_id, "stars": p.stars} for p in parts
        ],
        "runners": [
            {"runner_id": r.runner_id, "trap": r.trap, "name": r.name, "prob": r.prob}
            for r in runners
        ],
        "odds": [{"slot": o.slot, "odds": o.odds} for o in odds],
        "standings": [
            {
                "team_id": s.team_id, "ranking": s.ranking, "points": s.points,
                "wins": s.wins, "draws": s.draws, "losses": s.losses,
                "goals_for": s.goals_for, "goals_against": s.goals_against,
            }
            for s in standings
        ],
    }


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
    event = session.get(Event, e_block_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"event {e_block_id} not found")
    return _event_payload(session, event)
