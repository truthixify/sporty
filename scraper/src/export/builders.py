"""Builders that turn DB rows into the dict shapes that get written to JSON
files in the export tree. Pure functions on top of a SQLAlchemy session."""

from __future__ import annotations

import re
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
    SchemaMarket,
    SchemaParticipant,
    Season,
    Standing,
)
from src.export.markets import humanize_odds, parse_won_markets, preload_markets


def slug(name: str | None) -> str:
    """Filesystem-safe slug for directory names. Lowercases, swaps spaces and
    punctuation for hyphens, collapses runs."""
    if not name:
        return "unknown"
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return s or "unknown"


def build_schema(session: Session, schema_id: int) -> dict[str, Any]:
    """Top-level metadata for one schema: description, teams roster, full
    markets dictionary."""
    s = session.get(Schema, schema_id)
    if s is None:
        raise ValueError(f"schema {schema_id} not found")

    teams = session.scalars(
        select(SchemaParticipant)
        .where(SchemaParticipant.schema_id == schema_id)
        .order_by(SchemaParticipant.team_id)
    ).all()
    markets = session.scalars(
        select(SchemaMarket)
        .where(SchemaMarket.schema_id == schema_id)
        .order_by(SchemaMarket.slot)
    ).all()

    return {
        "schema_id": s.schema_id,
        "product": s.product,
        "kind": s.kind,
        "description": s.description,
        "competition_type": s.competition_type,
        "competition_subtype": s.competition_subtype,
        "num_participants": s.num_participants,
        "is_two_legs_group": s.is_two_legs_group,
        "countdown_s": s.countdown_s,
        "library_id": s.library_id,
        "first_seen_ts": s.first_seen_ts,
        "last_seen_ts": s.last_seen_ts,
        "participants": [
            {
                "team_id": t.team_id,
                "name": t.name,
                "fifa_code": t.fifa_code,
                "stars": t.stars,
            }
            for t in teams
        ],
        "markets": [
            {
                "slot": m.slot,
                "market_id": m.market_id,
                "market_name": m.market_name,
                "odd_id": m.odd_id,
                "odd_name": m.odd_name,
            }
            for m in markets
        ],
    }


def build_season(session: Session, season_id: int) -> dict[str, Any]:
    """Season summary: index, dates, champion (if known), matchdays list,
    plus a `partial: true` flag when the season hasn't ended yet."""
    season = session.get(Season, season_id)
    if season is None:
        raise ValueError(f"season {season_id} not found")
    schema = session.get(Schema, season.schema_id)
    snaps = session.scalars(
        select(MatchDaySnapshot)
        .where(MatchDaySnapshot.season_id == season_id)
        .order_by(MatchDaySnapshot.phase, MatchDaySnapshot.match_day)
    ).all()
    return {
        "season_id": season.season_id,
        "schema_id": season.schema_id,
        "schema_description": schema.description if schema else None,
        "season_index": season.season_index,
        "started_at": season.started_at,
        "ended_at": season.ended_at,
        "matchdays_completed": season.matchdays_completed,
        "champion_team_id": season.champion_team_id,
        "runner_up_team_id": season.runner_up_team_id,
        "partial": season.ended_at is None,
        "matchdays": [
            {"phase": s.phase, "match_day": s.match_day, "finalized_ts": s.finalized_ts}
            for s in snaps
        ],
        "raw_summary": season.raw_summary,
    }


def build_matchday(
    session: Session,
    season_id: int,
    phase: str,
    match_day: int,
) -> dict[str, Any]:
    """The full bundled matchday view: matches with odds + standings after +
    summary aggregates. The raw `won_markets` strings are parsed against the
    schema's market dictionary; odds slots are humanized to
    `MarketName.OddName` keys.

    The match list comes from `snap.matches_json`'s `e_block_id` references
    rather than a fresh phase-filtered SELECT, because the aggregator stores
    `phase=""` for leagues regardless of what the underlying events' phase
    column says — so a phase-filtered query would miss them.
    """
    snap = session.get(MatchDaySnapshot, (season_id, phase, match_day))
    if snap is None:
        raise ValueError(f"snapshot ({season_id}, {phase!r}, {match_day}) not found")
    season = session.get(Season, season_id)
    schema = session.get(Schema, season.schema_id) if season else None
    schema_id = season.schema_id if season else None
    markets = preload_markets(session, [schema_id]).get(schema_id, []) if schema_id else []

    # Use the snap's e_block_id list as the canonical set of matches in this
    # matchday — the aggregator already grouped events by (phase, match_day)
    # at write time, so this is exactly the right set.
    snap_matches = snap.matches_json.get("matches", []) if snap.matches_json else []
    e_block_ids = [m["e_block_id"] for m in snap_matches if m.get("e_block_id") is not None]

    matches: list[dict[str, Any]] = []
    if e_block_ids:
        events = session.scalars(
            select(Event)
            .where(Event.e_block_id.in_(e_block_ids))
            .order_by(Event.event_time, Event.e_block_id)
        ).all()
        for e in events:
            matches.append(_build_football_match(session, e, markets))

    # Standings: prefer fresh rows from the standings table (richer than the
    # snap's denormalized copy if the parser captured more fields). Take them
    # from whichever event was last in the matchday.
    standings: list[dict[str, Any]] = []
    if e_block_ids:
        last_eb = e_block_ids[-1]
        standing_rows = session.scalars(
            select(Standing).where(Standing.e_block_id == last_eb).order_by(Standing.ranking)
        ).all()
        if standing_rows:
            standings = [
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
                for s in standing_rows
            ]
    if not standings and snap.standings_json:
        # Fallback to the denormalized copy
        standings = snap.standings_json.get("standings", [])

    return {
        "season_id": season_id,
        "phase": phase,
        "match_day": match_day,
        "finalized_ts": snap.finalized_ts,
        "schema": (
            {"id": schema.schema_id, "description": schema.description}
            if schema is not None else None
        ),
        "matches": matches,
        "standings_after": standings,
        "summary": snap.summary_json,
    }


def _build_football_match(
    session: Session,
    event: Event,
    markets: list[SchemaMarket],
) -> dict[str, Any]:
    sides = session.scalars(
        select(EventParticipantFootball).where(EventParticipantFootball.e_block_id == event.e_block_id)
    ).all()
    by_side = {s.side: s for s in sides}
    odds_rows = session.scalars(
        select(Odds).where(Odds.e_block_id == event.e_block_id).order_by(Odds.slot)
    ).all()
    home_p = by_side.get("home")
    away_p = by_side.get("away")
    return {
        "e_block_id": event.e_block_id,
        "event_time": event.event_time,
        "server_status": event.server_status,
        "settled_ts": event.settled_ts,
        "home": {
            "team_id": event.home_team_id,
            "stars": home_p.stars if home_p else None,
            "score": event.home_score,
        },
        "away": {
            "team_id": event.away_team_id,
            "stars": away_p.stars if away_p else None,
            "score": event.away_score,
        },
        "won_markets": parse_won_markets(event.won_markets, markets),
        "content_duration": event.content_duration,
        "odds": humanize_odds(odds_rows, markets),
    }


def build_race(session: Session, e_block_id: int) -> dict[str, Any]:
    """One race export: track, runners, result, won markets, full odds dict."""
    event = session.get(Event, e_block_id)
    if event is None:
        raise ValueError(f"event {e_block_id} not found")
    schema = session.get(Schema, event.schema_id)
    markets = preload_markets(session, [event.schema_id]).get(event.schema_id, [])

    runners = session.scalars(
        select(EventRunner).where(EventRunner.e_block_id == e_block_id).order_by(EventRunner.trap)
    ).all()
    odds_rows = session.scalars(
        select(Odds).where(Odds.e_block_id == e_block_id).order_by(Odds.slot)
    ).all()

    runner_by_id = {r.runner_id: r for r in runners}

    def _runner_summary(runner_id: str | None) -> dict[str, Any] | None:
        if runner_id is None:
            return None
        r = runner_by_id.get(runner_id)
        if r is None:
            return {"runner_id": runner_id}
        return {"trap": r.trap, "runner_id": r.runner_id, "name": r.name}

    final_order = event.final_order.split(",") if event.final_order else []

    return {
        "e_block_id": event.e_block_id,
        "schema": {"id": event.schema_id, "description": schema.description if schema else None},
        "product": event.product,
        "event_time": event.event_time,
        "server_status": event.server_status,
        "settled_ts": event.settled_ts,
        "track": {
            "surface": event.surface,
            "distance": event.distance,
            "weather": event.weather,
            "track_condition": event.track_condition,
        },
        "num_runners": event.num_runners,
        "runners": [
            {
                "trap": r.trap,
                "runner_id": r.runner_id,
                "name": r.name,
                "prob": r.prob,
                "form": r.form,
                "star": r.star,
                "ability": r.ability,
                "speed": r.speed,
                "stamina": r.stamina,
                "wins": r.wins,
                "place": r.place,
                "pace": r.pace,
                "forecast": r.forecast.split(",") if r.forecast else [],
            }
            for r in runners
        ],
        "result": {
            "winner": _runner_summary(event.winner_id),
            "second": _runner_summary(event.second_id),
            "third": _runner_summary(event.third_id),
            "final_order": final_order,
            "media_id": event.media_id,
        },
        "won_markets": parse_won_markets(event.won_markets, markets),
        "content_duration": event.content_duration,
        "odds": humanize_odds(odds_rows, markets),
    }


def build_manifest(
    session: Session,
    *,
    products: list[str],
    schema_count: int,
    event_count: int,
    season_count: int,
    matchday_count: int,
    race_count: int,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import time

    from src import __version__

    return {
        "exported_at": time.time(),
        "scraper_version": __version__,
        "products": products,
        "filters": filters or {},
        "counts": {
            "schemas": schema_count,
            "events": event_count,
            "seasons": season_count,
            "matchday_snapshots": matchday_count,
            "races": race_count,
        },
    }


def event_row_for_rollup(
    session: Session,
    event: Event,
    markets: list[SchemaMarket],
) -> dict[str, Any]:
    """Compact row used for the all-events.ndjson rollup. Includes humanized
    odds inline."""
    odds_rows = session.scalars(
        select(Odds).where(Odds.e_block_id == event.e_block_id).order_by(Odds.slot)
    ).all()
    base: dict[str, Any] = {
        "e_block_id": event.e_block_id,
        "schema_id": event.schema_id,
        "season_id": event.season_id,
        "product": event.product,
        "server_status": event.server_status,
        "event_time": event.event_time,
        "captured_ts": event.captured_ts,
        "settled_ts": event.settled_ts,
        "won_markets": parse_won_markets(event.won_markets, markets),
        "odds": humanize_odds(odds_rows, markets),
    }
    if event.product == "football":
        base.update({
            "match_day": event.match_day,
            "phase": event.phase,
            "leg_order": event.leg_order,
            "home_team_id": event.home_team_id,
            "away_team_id": event.away_team_id,
            "home_score": event.home_score,
            "away_score": event.away_score,
        })
    else:
        base.update({
            "num_runners": event.num_runners,
            "winner_id": event.winner_id,
            "second_id": event.second_id,
            "third_id": event.third_id,
            "final_order": event.final_order.split(",") if event.final_order else [],
            "surface": event.surface,
            "distance": event.distance,
            "weather": event.weather,
        })
    return base
