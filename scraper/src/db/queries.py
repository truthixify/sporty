"""High-level read queries used by the FastAPI routes. Consolidating the
multi-table joins here keeps route handlers thin and makes each query unit-
testable without spinning up the API."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    Event,
    EventParticipantFootball,
    EventRunner,
    MatchDaySnapshot,
    Odds,
    Schema,
    Season,
    Standing,
)


def event_detail(session: Session, e_block_id: int) -> dict[str, Any] | None:
    event = session.get(Event, e_block_id)
    if event is None:
        return None
    parts = session.scalars(
        select(EventParticipantFootball).where(EventParticipantFootball.e_block_id == e_block_id)
    ).all()
    runners = session.scalars(
        select(EventRunner).where(EventRunner.e_block_id == e_block_id)
    ).all()
    odds = session.scalars(
        select(Odds).where(Odds.e_block_id == e_block_id).order_by(Odds.slot)
    ).all()
    standings = session.scalars(
        select(Standing).where(Standing.e_block_id == e_block_id).order_by(Standing.ranking)
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


def football_schemas_with_counts(session: Session) -> list[dict[str, Any]]:
    schemas = session.scalars(
        select(Schema).where(Schema.product == "football").order_by(Schema.schema_id)
    ).all()
    out: list[dict[str, Any]] = []
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
    return out


def season_summary(session: Session, season_id: int) -> dict[str, Any] | None:
    season = session.get(Season, season_id)
    if season is None:
        return None
    matchday_rows = session.scalars(
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
        "matchdays": [s.match_day for s in matchday_rows],
    }


def matchday_view(
    session: Session,
    season_id: int,
    match_day: int,
    phase: str = "",
) -> dict[str, Any] | None:
    """Return one matchday snapshot. For league seasons, `phase=""` is correct.
    For tournaments, the caller must pass the phase ("GROUPS", "KNOCKOUT",
    "FINAL") since the same `match_day` value can recur across phases."""
    snap = session.get(MatchDaySnapshot, (season_id, phase.upper() if phase else "", match_day))
    if snap is None:
        return None
    season = session.get(Season, season_id)
    schema = session.get(Schema, season.schema_id) if season is not None else None
    return {
        "season_id": season_id,
        "phase": snap.phase,
        "match_day": match_day,
        "finalized_ts": snap.finalized_ts,
        "schema": (
            {"id": schema.schema_id, "description": schema.description} if schema else None
        ),
        "matches": snap.matches_json.get("matches", []),
        "standings": snap.standings_json.get("standings", []),
        "summary": snap.summary_json,
    }


def final_standings(session: Session, season_id: int) -> dict[str, Any] | None:
    season = session.get(Season, season_id)
    if season is None:
        return None
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


def standings_at_time(
    session: Session,
    schema_id: int,
    time_iso: str,
) -> dict[str, Any] | None:
    s = session.get(Schema, schema_id)
    if s is None or s.product != "football":
        return None
    candidate = session.scalar(
        select(Event)
        .where(
            Event.schema_id == schema_id,
            Event.event_time.is_not(None),
            Event.event_time <= time_iso,
            Event.match_day.is_not(None),
        )
        .order_by(Event.event_time.desc())
        .limit(1)
    )
    if candidate is None:
        return {"schema_id": schema_id, "standings": []}
    standings = session.scalars(
        select(Standing).where(Standing.e_block_id == candidate.e_block_id).order_by(Standing.ranking)
    ).all()
    return {
        "schema_id": schema_id,
        "as_of": candidate.event_time,
        "match_day": candidate.match_day,
        "standings": [
            {"team_id": s.team_id, "ranking": s.ranking, "points": s.points,
             "wins": s.wins, "draws": s.draws, "losses": s.losses,
             "goals_for": s.goals_for, "goals_against": s.goals_against}
            for s in standings
        ],
    }
