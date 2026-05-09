from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from src.api.deps import SessionDep
from src.db import queries
from src.db.models import MatchDaySnapshot, Schema, Season


router = APIRouter()


@router.get("/football/schemas")
def list_football_schemas(session: SessionDep) -> dict:
    schemas = queries.football_schemas_with_counts(session)
    return {"count": len(schemas), "schemas": schemas}


@router.get("/football/schemas/{schema_id}/seasons")
def list_seasons(session: SessionDep, schema_id: int) -> dict:
    schema = session.get(Schema, schema_id)
    if schema is None or schema.product != "football":
        raise HTTPException(status_code=404, detail="football schema not found")
    seasons = session.scalars(
        select(Season).where(Season.schema_id == schema_id).order_by(Season.season_index)
    ).all()
    return {"schema_id": schema_id, "seasons": [
        {
            "season_id": s.season_id,
            "season_index": s.season_index,
            "started_at": s.started_at,
            "ended_at": s.ended_at,
            "matchdays_completed": s.matchdays_completed,
            "champion_team_id": s.champion_team_id,
            "runner_up_team_id": s.runner_up_team_id,
        }
        for s in seasons
    ]}


@router.get("/football/seasons/{season_id}")
def season_summary(session: SessionDep, season_id: int) -> dict:
    out = queries.season_summary(session, season_id)
    if out is None:
        raise HTTPException(status_code=404, detail="season not found")
    return out


@router.get("/football/seasons/{season_id}/matchdays")
def list_matchdays(session: SessionDep, season_id: int) -> dict:
    if session.get(Season, season_id) is None:
        raise HTTPException(status_code=404, detail="season not found")
    snaps = session.scalars(
        select(MatchDaySnapshot)
        .where(MatchDaySnapshot.season_id == season_id)
        .order_by(MatchDaySnapshot.phase, MatchDaySnapshot.match_day)
    ).all()
    return {"season_id": season_id, "matchdays": [
        {
            "phase": s.phase,
            "match_day": s.match_day,
            "finalized_ts": s.finalized_ts,
            "summary": s.summary_json,
        }
        for s in snaps
    ]}


@router.get("/football/seasons/{season_id}/matchdays/{match_day}")
def matchday_view(
    session: SessionDep,
    season_id: int,
    match_day: int,
    phase: str = Query(
        "",
        description="Phase for tournaments (GROUPS/KNOCKOUT/FINAL). Empty for leagues.",
    ),
) -> dict:
    out = queries.matchday_view(session, season_id, match_day, phase)
    if out is None:
        raise HTTPException(status_code=404, detail="matchday snapshot not found")
    return out


@router.get("/football/seasons/{season_id}/standings/final")
def final_standings(session: SessionDep, season_id: int) -> dict:
    out = queries.final_standings(session, season_id)
    if out is None:
        raise HTTPException(status_code=404, detail="season not found")
    return out


@router.get("/football/standings/at")
def standings_at(
    session: SessionDep,
    schema: int = Query(..., description="Football schema id."),
    time_iso: str = Query(..., alias="time", description="ISO 8601 cutoff. Returns latest snapshot at or before this."),
) -> dict:
    out = queries.standings_at_time(session, schema, time_iso)
    if out is None:
        raise HTTPException(status_code=404, detail="football schema not found")
    return out
