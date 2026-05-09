from __future__ import annotations

from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all scraper tables."""


class Schema(Base):
    """Schema template (league or tournament), one row per upstream `schema_id`."""

    __tablename__ = "schemas"

    schema_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    product: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    description_tag: Mapped[str | None] = mapped_column(Text)
    competition_type: Mapped[str | None] = mapped_column(Text)
    competition_subtype: Mapped[str | None] = mapped_column(Text)
    countdown_s: Mapped[int | None] = mapped_column(Integer)
    market_template_id: Mapped[int | None] = mapped_column(Integer)
    library_id: Mapped[str | None] = mapped_column(Text)
    content_library: Mapped[str | None] = mapped_column(Text)
    num_participants: Mapped[int | None] = mapped_column(Integer)
    is_two_legs_group: Mapped[bool | None] = mapped_column(Boolean)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    first_seen_ts: Mapped[float] = mapped_column(Float, nullable=False)
    last_seen_ts: Mapped[float] = mapped_column(Float, nullable=False)


class SchemaMarket(Base):
    __tablename__ = "schema_markets"

    schema_id: Mapped[int] = mapped_column(
        ForeignKey("schemas.schema_id"), primary_key=True
    )
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[str] = mapped_column(Text, nullable=False)
    market_name: Mapped[str] = mapped_column(Text, nullable=False)
    odd_id: Mapped[str] = mapped_column(Text, nullable=False)
    odd_name: Mapped[str] = mapped_column(Text, nullable=False)


class SchemaParticipant(Base):
    __tablename__ = "schema_participants"

    schema_id: Mapped[int] = mapped_column(
        ForeignKey("schemas.schema_id"), primary_key=True
    )
    team_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    fifa_code: Mapped[str | None] = mapped_column(Text)
    stars: Mapped[float | None] = mapped_column(Float)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Season(Base):
    """Season instance, one row per (schema, completed cycle)."""

    __tablename__ = "seasons"
    __table_args__ = (
        UniqueConstraint("schema_id", "season_index", name="uq_seasons_schema_index"),
        Index("idx_seasons_schema", "schema_id"),
    )

    season_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    schema_id: Mapped[int] = mapped_column(
        ForeignKey("schemas.schema_id"), nullable=False
    )
    season_index: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    ended_at: Mapped[str | None] = mapped_column(Text)
    matchdays_completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    champion_team_id: Mapped[str | None] = mapped_column(Text)
    runner_up_team_id: Mapped[str | None] = mapped_column(Text)
    raw_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Event(Base):
    """One match or one race. Primary key is the upstream `eBlockId`."""

    __tablename__ = "events"
    __table_args__ = (
        Index("idx_events_schema_time", "schema_id", "event_time"),
        Index("idx_events_season_md", "season_id", "match_day"),
        Index("idx_events_product", "product"),
        Index("idx_events_status", "server_status"),
        Index("idx_events_settled_ts", "settled_ts"),
    )

    e_block_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    schema_id: Mapped[int] = mapped_column(
        ForeignKey("schemas.schema_id"), nullable=False
    )
    season_id: Mapped[int | None] = mapped_column(ForeignKey("seasons.season_id"))
    product: Mapped[str] = mapped_column(Text, nullable=False)
    server_status: Mapped[str | None] = mapped_column(Text)
    event_time: Mapped[str | None] = mapped_column(Text)
    captured_ts: Mapped[float] = mapped_column(Float, nullable=False)
    settled_ts: Mapped[float | None] = mapped_column(Float)

    home_team_id: Mapped[str | None] = mapped_column(Text)
    away_team_id: Mapped[str | None] = mapped_column(Text)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    phase: Mapped[str | None] = mapped_column(Text)
    match_day: Mapped[int | None] = mapped_column(Integer)
    leg_order: Mapped[int | None] = mapped_column(Integer)
    week_day: Mapped[int | None] = mapped_column(Integer)
    champ_id: Mapped[int | None] = mapped_column(Integer)

    num_runners: Mapped[int | None] = mapped_column(Integer)
    winner_id: Mapped[str | None] = mapped_column(Text)
    second_id: Mapped[str | None] = mapped_column(Text)
    third_id: Mapped[str | None] = mapped_column(Text)
    final_order: Mapped[str | None] = mapped_column(Text)
    track_condition: Mapped[float | None] = mapped_column(Float)
    weather: Mapped[str | None] = mapped_column(Text)
    surface: Mapped[str | None] = mapped_column(Text)
    distance: Mapped[float | None] = mapped_column(Float)

    won_markets: Mapped[str | None] = mapped_column(Text)
    won_market_count: Mapped[int | None] = mapped_column(Integer)
    content_duration: Mapped[float | None] = mapped_column(Float)
    media_id: Mapped[str | None] = mapped_column(Text)

    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    raw_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Odds(Base):
    """Closing-snapshot odds, one row per slot per event."""

    __tablename__ = "odds"

    e_block_id: Mapped[int] = mapped_column(
        ForeignKey("events.e_block_id"), primary_key=True
    )
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    odds: Mapped[float] = mapped_column(Float, nullable=False)


class OddsHistory(Base):
    """Time-series odds, populated only when movement is observed."""

    __tablename__ = "odds_history"

    e_block_id: Mapped[int] = mapped_column(
        ForeignKey("events.e_block_id"), primary_key=True
    )
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_ts: Mapped[float] = mapped_column(Float, primary_key=True)
    odds: Mapped[float] = mapped_column(Float, nullable=False)


class EventParticipantFootball(Base):
    __tablename__ = "event_participants_football"

    e_block_id: Mapped[int] = mapped_column(
        ForeignKey("events.e_block_id"), primary_key=True
    )
    side: Mapped[str] = mapped_column(Text, primary_key=True)
    team_id: Mapped[str] = mapped_column(Text, nullable=False)
    stars: Mapped[float | None] = mapped_column(Float)


class EventRunner(Base):
    __tablename__ = "event_runners"

    e_block_id: Mapped[int] = mapped_column(
        ForeignKey("events.e_block_id"), primary_key=True
    )
    runner_id: Mapped[str] = mapped_column(Text, primary_key=True)
    trap: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(Text)
    prob: Mapped[float | None] = mapped_column(Float)
    form: Mapped[float | None] = mapped_column(Float)
    star: Mapped[int | None] = mapped_column(Integer)
    ability: Mapped[float | None] = mapped_column(Float)
    speed: Mapped[float | None] = mapped_column(Float)
    stamina: Mapped[float | None] = mapped_column(Float)
    wins: Mapped[float | None] = mapped_column(Float)
    place: Mapped[float | None] = mapped_column(Float)
    pace: Mapped[str | None] = mapped_column(Text)
    forecast: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Standing(Base):
    """Football standings snapshot recorded against an event."""

    __tablename__ = "standings"
    __table_args__ = (Index("idx_standings_season_md", "season_id", "match_day"),)

    e_block_id: Mapped[int] = mapped_column(
        ForeignKey("events.e_block_id"), primary_key=True
    )
    team_id: Mapped[str] = mapped_column(Text, primary_key=True)
    season_id: Mapped[int | None] = mapped_column(ForeignKey("seasons.season_id"))
    match_day: Mapped[int | None] = mapped_column(Integer)
    ranking: Mapped[int | None] = mapped_column(Integer)
    points: Mapped[int | None] = mapped_column(Integer)
    wins: Mapped[int | None] = mapped_column(Integer)
    draws: Mapped[int | None] = mapped_column(Integer)
    losses: Mapped[int | None] = mapped_column(Integer)
    goals_for: Mapped[int | None] = mapped_column(Integer)
    goals_against: Mapped[int | None] = mapped_column(Integer)
    goal_diff: Mapped[int | None] = mapped_column(Integer)
    history: Mapped[str | None] = mapped_column(Text)


class MatchDaySnapshot(Base):
    """Denormalized per-matchday view: matches plus standings plus summary."""

    __tablename__ = "match_day_snapshots"

    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.season_id"), primary_key=True
    )
    # Empty string for league seasons; "GROUPS" / "KNOCKOUT" / "FINAL" for
    # tournaments where the same match_day exists in multiple phases.
    phase: Mapped[str] = mapped_column(Text, primary_key=True, default="", server_default="")
    match_day: Mapped[int] = mapped_column(Integer, primary_key=True)
    finalized_ts: Mapped[float | None] = mapped_column(Float)
    matches_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    standings_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ParseWatermark(Base):
    __tablename__ = "parse_watermarks"

    journal_file: Mapped[str] = mapped_column(Text, primary_key=True)
    last_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    last_line: Mapped[int] = mapped_column(Integer, nullable=False)
    parsed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    last_run_ts: Mapped[float] = mapped_column(Float, nullable=False)


class CaptureSession(Base):
    __tablename__ = "capture_sessions"

    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    started_ts: Mapped[float] = mapped_column(Float, nullable=False)
    ended_ts: Mapped[float | None] = mapped_column(Float)
    end_reason: Mapped[str | None] = mapped_column(Text)
    frames_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    frames_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class MetricsSnapshot(Base):
    __tablename__ = "metrics_snapshots"

    snapshot_ts: Mapped[float] = mapped_column(Float, primary_key=True)
    frames_per_min: Mapped[float | None] = mapped_column(Float)
    events_per_hour: Mapped[float | None] = mapped_column(Float)
    error_rate: Mapped[float | None] = mapped_column(Float)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AlertLog(Base):
    __tablename__ = "alerts_log"

    alert_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fired_ts: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    channels: Mapped[str | None] = mapped_column(Text)
    delivered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
