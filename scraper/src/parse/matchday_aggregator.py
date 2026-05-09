from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Event, MatchDaySnapshot, Season, Standing


def aggregate_matchdays(session: Session) -> int:
    """Build a `MatchDaySnapshot` row for every (season, match_day) where every
    match has settled. Idempotent via session.merge on the composite PK.

    Returns the number of snapshots written/updated.
    """
    seasons = session.scalars(select(Season).order_by(Season.season_id)).all()
    written = 0

    for season in seasons:
        events = session.scalars(
            select(Event)
            .where(Event.season_id == season.season_id, Event.product == "football")
            .order_by(Event.match_day, Event.event_time, Event.e_block_id)
        ).all()
        if not events:
            continue

        by_md: dict[int, list[Event]] = {}
        for e in events:
            if e.match_day is None:
                continue
            by_md.setdefault(e.match_day, []).append(e)

        for match_day, evts in by_md.items():
            if not all(e.settled_ts is not None for e in evts):
                continue
            snapshot = _build_snapshot(season.season_id, match_day, evts, session)
            session.merge(snapshot)
            written += 1

    session.flush()
    return written


def _build_snapshot(
    season_id: int,
    match_day: int,
    events: list[Event],
    session: Session,
) -> MatchDaySnapshot:
    last_event = max(events, key=lambda e: (e.event_time or "", e.e_block_id))
    standings = session.scalars(
        select(Standing)
        .where(Standing.e_block_id == last_event.e_block_id)
        .order_by(Standing.ranking)
    ).all()

    matches: list[dict[str, Any]] = [
        {
            "e_block_id": e.e_block_id,
            "event_time": e.event_time,
            "home_team_id": e.home_team_id,
            "away_team_id": e.away_team_id,
            "home_score": e.home_score,
            "away_score": e.away_score,
            "won_markets": e.won_markets.split(",") if e.won_markets else [],
            "phase": e.phase,
        }
        for e in events
    ]
    standings_payload: list[dict[str, Any]] = [
        {
            "team_id": s.team_id,
            "ranking": s.ranking,
            "points": s.points,
            "wins": s.wins,
            "draws": s.draws,
            "losses": s.losses,
            "goals_for": s.goals_for,
            "goals_against": s.goals_against,
            "goal_diff": s.goal_diff,
        }
        for s in standings
    ]

    home_wins = sum(1 for e in events if _gt(e.home_score, e.away_score))
    away_wins = sum(1 for e in events if _gt(e.away_score, e.home_score))
    draws = sum(
        1 for e in events
        if e.home_score is not None and e.away_score is not None and e.home_score == e.away_score
    )
    total_goals = sum((e.home_score or 0) + (e.away_score or 0) for e in events)

    biggest_win = None
    biggest_diff = -1
    for e in events:
        if e.home_score is None or e.away_score is None:
            continue
        diff = abs(e.home_score - e.away_score)
        if diff > biggest_diff:
            biggest_diff = diff
            biggest_win = {
                "e_block_id": e.e_block_id,
                "home_team_id": e.home_team_id,
                "away_team_id": e.away_team_id,
                "home_score": e.home_score,
                "away_score": e.away_score,
            }

    summary: dict[str, Any] = {
        "matches_count": len(events),
        "total_goals": total_goals,
        "home_wins": home_wins,
        "away_wins": away_wins,
        "draws": draws,
        "biggest_win": biggest_win,
    }
    finalized_ts = max((e.settled_ts or 0.0) for e in events)

    return MatchDaySnapshot(
        season_id=season_id,
        match_day=match_day,
        finalized_ts=finalized_ts,
        matches_json={"matches": matches},
        standings_json={"standings": standings_payload},
        summary_json=summary,
    )


def _gt(a: int | None, b: int | None) -> bool:
    if a is None or b is None:
        return False
    return a > b
