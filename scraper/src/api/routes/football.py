from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from src.api.deps import SessionDep
from src.db import queries
from src.db.models import MatchDaySnapshot, Schema, Season


router = APIRouter(tags=["Football"])


@router.get(
    "/football/schemas",
    summary="List football schemas",
    description=(
        "Every football schema in the dataset, with detected season count "
        "and event count. `kind` is `league` (recurring 38-matchday season) "
        "or `tournament` (GROUPS -> KNOCKOUT -> FINAL). `unknown` shows up "
        "when only event data was seen and the schema's own `/playlists/` "
        "frame hasn't arrived yet."
    ),
)
def list_football_schemas(session: SessionDep) -> dict:
    schemas = queries.football_schemas_with_counts(session)
    return {"count": len(schemas), "schemas": schemas}


@router.get(
    "/football/schemas/{schema_id}/seasons",
    summary="List seasons for one football schema",
    description=(
        "All seasons detected for the given schema, ordered by season index. "
        "For leagues, a season is one full 38-matchday cycle (boundary "
        "detected when matchday goes 38 -> 1). For tournaments, a season is "
        "one full GROUPS -> KNOCKOUT -> FINAL run (boundary on phase reset)."
    ),
    responses={404: {"description": "Schema not found, or schema is not football."}},
)
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


@router.get(
    "/football/seasons/{season_id}",
    summary="Season summary",
    description=(
        "Top-level summary for one season: index, start/end timestamps, "
        "matchdays completed, champion / runner-up if known, and the list "
        "of matchday numbers that have been finalized."
    ),
    responses={404: {"description": "Season id not found."}},
)
def season_summary(session: SessionDep, season_id: int) -> dict:
    out = queries.season_summary(session, season_id)
    if out is None:
        raise HTTPException(status_code=404, detail="season not found")
    return out


@router.get(
    "/football/seasons/{season_id}/matchdays",
    summary="List matchdays in a season",
    description=(
        "Returns every matchday snapshot that exists for this season, with "
        "summary aggregates (total goals, win counts, biggest win) but NOT "
        "the full match list - use the per-matchday endpoint for that. "
        "Tournaments include `phase`; leagues just leave it empty."
    ),
    responses={404: {"description": "Season id not found."}},
)
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


@router.get(
    "/football/seasons/{season_id}/matchdays/{match_day}",
    summary="One matchday: matches + standings + summary",
    description=(
        "The bundled view for one matchday. `matches` is the full list of "
        "matches played that day with scores and won_markets; `standings` "
        "is the league table AFTER the matchday finished; `summary` rolls "
        "up totals (goals, home/away/draw counts, biggest win). For "
        "tournaments where the same `match_day` value can recur in "
        "different phases (e.g. quarter-final leg 1 vs final leg 1), pass "
        "the `phase` query param. Leagues leave it empty."
    ),
    responses={404: {"description": "No snapshot at that (season, phase, match_day)."}},
)
def matchday_view(
    session: SessionDep,
    season_id: int,
    match_day: int,
    phase: str = Query(
        "",
        description=(
            "Phase for tournaments (`GROUPS` / `KNOCKOUT` / `FINAL`). "
            "Leave empty for leagues."
        ),
    ),
) -> dict:
    out = queries.matchday_view(session, season_id, match_day, phase)
    if out is None:
        raise HTTPException(status_code=404, detail="matchday snapshot not found")
    return out


@router.get(
    "/football/seasons/{season_id}/standings/final",
    summary="Final standings of a season",
    description=(
        "The standings from the latest matchday snapshot for this season. "
        "If the season is still in progress, this returns the most recent "
        "completed matchday's standings."
    ),
    responses={404: {"description": "Season id not found."}},
)
def final_standings(session: SessionDep, season_id: int) -> dict:
    out = queries.final_standings(session, season_id)
    if out is None:
        raise HTTPException(status_code=404, detail="season not found")
    return out


@router.get(
    "/football/standings/at",
    summary="Standings as of a point in time",
    description=(
        "Returns the standings snapshot captured against the latest event "
        "in `schema` at or before `time`. Useful for 'what did the table "
        "look like before kickoff on date X' queries."
    ),
    responses={404: {"description": "Football schema not found."}},
)
def standings_at(
    session: SessionDep,
    schema: int = Query(..., description="Football schema id."),
    time_iso: str = Query(
        ...,
        alias="time",
        description="ISO 8601 cutoff. We return the latest snapshot at or before this.",
    ),
) -> dict:
    out = queries.standings_at_time(session, schema, time_iso)
    if out is None:
        raise HTTPException(status_code=404, detail="football schema not found")
    return out
