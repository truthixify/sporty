"""Schema-level tests for the SQLAlchemy models. Most model behaviour is
exercised through the parser/API tests; the cases here are about the
constraints themselves: PK enforcement, NULLability, JSON round-trip, the
composite PK on `match_day_snapshots`."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.config import Config, Database, Paths
from src.db import init_db, make_engine, make_session_factory
from src.db.models import (
    AlertLog,
    CaptureSession,
    Event,
    MatchDaySnapshot,
    MetricsSnapshot,
    Odds,
    OddsHistory,
    ParseWatermark,
    Schema,
    Season,
)


@pytest.fixture
def session_factory(tmp_path: Path):
    cfg = Config(
        paths=Paths(data_dir=tmp_path),
        database=Database(url=f"sqlite:///{tmp_path / 'm.db'}"),
    )
    init_db(config=cfg)
    return make_session_factory(make_engine(cfg))


def test_schema_pk_is_unique(session_factory) -> None:
    with session_factory() as s:
        s.add(Schema(schema_id=1, product="football", kind="league",
                     first_seen_ts=0, last_seen_ts=0))
        s.commit()
        s.add(Schema(schema_id=1, product="football", kind="league",
                     first_seen_ts=0, last_seen_ts=0))
        with pytest.raises(IntegrityError):
            s.commit()


def test_event_required_fields(session_factory) -> None:
    with session_factory() as s:
        s.add(Schema(schema_id=1, product="football", kind="league",
                     first_seen_ts=0, last_seen_ts=0))
        s.flush()
        # captured_ts is NOT NULL; product is NOT NULL
        s.add(Event(e_block_id=1, schema_id=1, product="football",
                    captured_ts=1.0))
        s.commit()
        loaded = s.get(Event, 1)
        assert loaded is not None
        assert loaded.product == "football"


def test_match_day_snapshot_composite_pk_with_phase(session_factory) -> None:
    with session_factory() as s:
        s.add(Schema(schema_id=1, product="football", kind="tournament",
                     first_seen_ts=0, last_seen_ts=0))
        season = Season(schema_id=1, season_index=1, started_at="2026-05-09T00:00:00Z")
        s.add(season)
        s.flush()

        # Two snapshots with same (season, match_day) but different phases:
        # this is exactly the case the new PK was added to support.
        s.add(MatchDaySnapshot(
            season_id=season.season_id, phase="GROUPS", match_day=1,
            matches_json={}, standings_json={},
        ))
        s.add(MatchDaySnapshot(
            season_id=season.season_id, phase="KNOCKOUT", match_day=1,
            matches_json={}, standings_json={},
        ))
        s.commit()
        rows = s.scalars(select(MatchDaySnapshot)).all()
        assert {(r.phase, r.match_day) for r in rows} == {("GROUPS", 1), ("KNOCKOUT", 1)}


def test_odds_history_pk_includes_snapshot_ts(session_factory) -> None:
    with session_factory() as s:
        s.add(Schema(schema_id=1, product="football", kind="league",
                     first_seen_ts=0, last_seen_ts=0))
        s.flush()
        s.add(Event(e_block_id=10, schema_id=1, product="football", captured_ts=1.0))
        s.flush()
        s.add(OddsHistory(e_block_id=10, slot=0, snapshot_ts=1.0, odds=2.0))
        s.add(OddsHistory(e_block_id=10, slot=0, snapshot_ts=2.0, odds=2.1))
        s.commit()
        rows = s.scalars(select(OddsHistory).where(OddsHistory.e_block_id == 10)).all()
        assert len(rows) == 2


def test_json_columns_round_trip_dicts(session_factory) -> None:
    with session_factory() as s:
        payload = {"nested": {"k": [1, 2, 3]}, "n": None, "f": 1.5}
        s.add(MetricsSnapshot(snapshot_ts=1.0, raw=payload))
        s.commit()
        loaded = s.scalars(select(MetricsSnapshot)).all()
        assert loaded[0].raw == payload


def test_capture_session_default_counters(session_factory) -> None:
    with session_factory() as s:
        s.add(CaptureSession(session_id="abc", started_ts=1.0))
        s.commit()
        loaded = s.get(CaptureSession, "abc")
        assert loaded is not None
        assert loaded.frames_in == 0
        assert loaded.frames_out == 0
        assert loaded.error_count == 0


def test_alert_log_autoincrement_pk(session_factory) -> None:
    with session_factory() as s:
        s.add(AlertLog(fired_ts=1.0, severity="info", title="t1"))
        s.add(AlertLog(fired_ts=2.0, severity="warning", title="t2"))
        s.commit()
        ids = sorted(r.alert_id for r in s.scalars(select(AlertLog)).all())
        assert ids[0] is not None and ids[1] is not None
        assert ids[1] > ids[0]


def test_parse_watermark_pk_is_journal_file(session_factory) -> None:
    with session_factory() as s:
        s.add(ParseWatermark(
            journal_file="/tmp/a.jsonl", last_offset=100, last_line=5,
            parsed_count=5, last_run_ts=1.0,
        ))
        s.commit()
        with pytest.raises(IntegrityError):
            s.add(ParseWatermark(
                journal_file="/tmp/a.jsonl", last_offset=200, last_line=10,
                parsed_count=10, last_run_ts=2.0,
            ))
            s.commit()


def test_odds_pk_is_event_and_slot(session_factory) -> None:
    with session_factory() as s:
        s.add(Schema(schema_id=1, product="football", kind="league",
                     first_seen_ts=0, last_seen_ts=0))
        s.flush()
        s.add(Event(e_block_id=1, schema_id=1, product="football", captured_ts=1.0))
        s.flush()
        s.add(Odds(e_block_id=1, slot=0, odds=1.5))
        s.add(Odds(e_block_id=1, slot=1, odds=2.5))
        s.commit()
        with pytest.raises(IntegrityError):
            s.add(Odds(e_block_id=1, slot=0, odds=99.9))
            s.commit()
