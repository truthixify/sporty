from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from src.api.deps import SessionDep
from src.db.models import Event, MatchDaySnapshot, Schema, Season, Standing


router = APIRouter()


@router.get("/football/schemas")
def list_football_schemas(session: SessionDep) -> dict:
    schemas = session.scalars(
        select(Schema).where(Schema.product == "football").order_by(Schema.schema_id)
    ).all()
    out = []
    for s in schemas:
        seasons_count = session.scalar(
            select(func.count()).select_from(Season).where(Season.schema_id == s.schema_id)
        ) or 0
        events_count = session.scalar(
            select(func.count()).select_from(Event).where(Event.schema_id == s.schema_id)
        ) or 0
        out.append({
            "schema_id": s.schema_id,
            "kind": s.kind,
            "description": s.description,
            "competition_type": s.competition_type,
            "num_participants": s.num_participants,
            "is_two_legs_group": s.is_two_legs_group,
            "seasons": int(seasons_count),
            "events": int(events_count),
        })
    return {"count": len(out), "schemas": out}


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
    season = session.get(Season, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="season not found")
    snapshots = session.scalars(
        select(MatchDaySnapshot)
        .where(MatchDaySnapshot.season_id == season_id)
        .order_by(MatchDaySnapshot.match_day)
    ).all()
    return {
        "season_id": season.season_id,
        "schema_id": season.schema_id,
        "season_index": season.season_index,
        "started_at": season.started_at,
        "ended_at": season.ended_at,
        "matchdays_completed": season.matchdays_completed,
        "champion_team_id": season.champion_team_id,
        "runner_up_team_id": season.runner_up_team_id,
        "matchdays": [s.match_day for s in snapshots],
    }


@router.get("/football/seasons/{season_id}/matchdays")
def list_matchdays(session: SessionDep, season_id: int) -> dict:
    season = session.get(Season, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="season not found")
    snaps = session.scalars(
        select(MatchDaySnapshot)
        .where(MatchDaySnapshot.season_id == season_id)
        .order_by(MatchDaySnapshot.match_day)
    ).all()
    return {"season_id": season_id, "matchdays": [
        {"match_day": s.match_day, "finalized_ts": s.finalized_ts, "summary": s.summary_json}
        for s in snaps
    ]}


@router.get("/football/seasons/{season_id}/matchdays/{match_day}")
def matchday_view(session: SessionDep, season_id: int, match_day: int) -> dict:
    snap = session.get(MatchDaySnapshot, (season_id, match_day))
    if snap is None:
        raise HTTPException(status_code=404, detail="matchday snapshot not found")
    schema = None
    season = session.get(Season, season_id)
    if season is not None:
        schema = session.get(Schema, season.schema_id)
    return {
        "season_id": season_id,
        "match_day": match_day,
        "finalized_ts": snap.finalized_ts,
        "schema": (
            {"id": schema.schema_id, "description": schema.description} if schema else None
        ),
        "matches": snap.matches_json.get("matches", []),
        "standings": snap.standings_json.get("standings", []),
        "summary": snap.summary_json,
    }


@router.get("/football/seasons/{season_id}/standings/final")
def final_standings(session: SessionDep, season_id: int) -> dict:
    season = session.get(Season, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="season not found")
    snap = session.scalar(
        select(MatchDaySnapshot)
        .where(MatchDaySnapshot.season_id == season_id)
        .order_by(MatchDaySnapshot.match_day.desc())
        .limit(1)
    )
    if snap is None:
        return {"season_id": season_id, "standings": []}
    return {
        "season_id": season_id,
        "match_day": snap.match_day,
        "standings": snap.standings_json.get("standings", []),
    }


@router.get("/football/standings/at")
def standings_at(
    session: SessionDep,
    schema: int = Query(..., description="Football schema id."),
    time_iso: str = Query(..., alias="time", description="ISO 8601 cutoff. Returns latest snapshot at or before this."),
) -> dict:
    s = session.get(Schema, schema)
    if s is None or s.product != "football":
        raise HTTPException(status_code=404, detail="football schema not found")
    seasons = session.scalars(
        select(Season).where(Season.schema_id == schema).order_by(Season.season_index)
    ).all()
    candidate_event = session.scalar(
        select(Event)
        .where(
            Event.schema_id == schema,
            Event.event_time.is_not(None),
            Event.event_time <= time_iso,
            Event.match_day.is_not(None),
        )
        .order_by(Event.event_time.desc())
        .limit(1)
    )
    if candidate_event is None:
        return {"schema_id": schema, "standings": []}
    standings = session.scalars(
        select(Standing).where(Standing.e_block_id == candidate_event.e_block_id).order_by(Standing.ranking)
    ).all()
    return {
        "schema_id": schema,
        "as_of": candidate_event.event_time,
        "match_day": candidate_event.match_day,
        "standings": [
            {"team_id": s.team_id, "ranking": s.ranking, "points": s.points,
             "wins": s.wins, "draws": s.draws, "losses": s.losses,
             "goals_for": s.goals_for, "goals_against": s.goals_against}
            for s in standings
        ],
    }
