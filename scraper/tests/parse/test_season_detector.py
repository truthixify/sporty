from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from src.config import Config, Database, Paths
from src.db import init_db, make_engine, make_session_factory
from src.db.models import Event, Schema, Season, Standing
from src.parse.season_detector import detect_seasons


def _session(tmp_path: Path):
    cfg = Config(paths=Paths(data_dir=tmp_path), database=Database(url=f"sqlite:///{tmp_path / 's.db'}"))
    init_db(config=cfg)
    engine = make_engine(cfg)
    return make_session_factory(engine)()


def _seed_schema(session, schema_id: int = 1, kind: str = "league") -> None:
    session.add(Schema(
        schema_id=schema_id, product="football", kind=kind,
        first_seen_ts=0.0, last_seen_ts=0.0,
    ))


def _seed_event(session, *, e_block_id: int, schema_id: int, match_day: int, event_time: str, settled_ts: float | None = 1.0) -> None:
    session.add(Event(
        e_block_id=e_block_id, schema_id=schema_id, product="football",
        match_day=match_day, event_time=event_time, captured_ts=1.0, settled_ts=settled_ts,
    ))


def _seed_standing(session, *, e_block_id: int, team_id: str, points: int) -> None:
    session.add(Standing(e_block_id=e_block_id, team_id=team_id, points=points))


def test_single_season_assigned_when_matchday_increases(tmp_path: Path) -> None:
    s = _session(tmp_path)
    _seed_schema(s)
    for i, md in enumerate([5, 6, 7, 8], start=1):
        _seed_event(s, e_block_id=i, schema_id=1, match_day=md, event_time=f"2026-05-09T1{i}:00:00Z")
    s.flush()

    new = detect_seasons(s, require_standings_reset=False)
    assert new == 1
    seasons = s.scalars(select(Season)).all()
    assert len(seasons) == 1
    assert seasons[0].season_index == 1
    assert seasons[0].matchdays_completed == 8
    assert all(e.season_id == seasons[0].season_id for e in s.scalars(select(Event)))
    s.close()


def test_season_boundary_detected_on_matchday_backwards(tmp_path: Path) -> None:
    s = _session(tmp_path)
    _seed_schema(s)
    for i, md in enumerate([36, 37, 38, 1, 2, 3], start=1):
        _seed_event(s, e_block_id=i, schema_id=1, match_day=md, event_time=f"2026-05-09T1{i}:00:00Z")
    s.flush()

    new = detect_seasons(s, require_standings_reset=False)
    assert new == 2
    seasons = sorted(s.scalars(select(Season)).all(), key=lambda x: x.season_index)
    assert [x.season_index for x in seasons] == [1, 2]
    assert seasons[0].matchdays_completed == 38
    assert seasons[1].matchdays_completed == 3

    by_eb = {e.e_block_id: e for e in s.scalars(select(Event))}
    assert by_eb[3].season_id == seasons[0].season_id
    assert by_eb[4].season_id == seasons[1].season_id
    s.close()


def test_require_standings_reset_ignores_spurious_backward(tmp_path: Path) -> None:
    s = _session(tmp_path)
    _seed_schema(s)
    for i, md in enumerate([10, 12, 11, 13], start=1):
        _seed_event(s, e_block_id=i, schema_id=1, match_day=md, event_time=f"2026-05-09T1{i}:00:00Z")
    _seed_standing(s, e_block_id=3, team_id="A", points=8)
    _seed_standing(s, e_block_id=3, team_id="B", points=5)
    s.flush()

    new = detect_seasons(s, require_standings_reset=True)
    assert new == 1
    s.close()


def test_require_standings_reset_accepts_when_points_zero(tmp_path: Path) -> None:
    s = _session(tmp_path)
    _seed_schema(s)
    for i, md in enumerate([37, 38, 1, 2], start=1):
        _seed_event(s, e_block_id=i, schema_id=1, match_day=md, event_time=f"2026-05-09T1{i}:00:00Z")
    _seed_standing(s, e_block_id=3, team_id="A", points=0)
    _seed_standing(s, e_block_id=3, team_id="B", points=0)
    s.flush()

    new = detect_seasons(s, require_standings_reset=True)
    assert new == 2
    s.close()


def test_tournament_boundary_on_phase_reset(tmp_path: Path) -> None:
    s = _session(tmp_path)
    _seed_schema(s, schema_id=10, kind="tournament")
    phases = [
        ("GROUPS", 1), ("GROUPS", 2),
        ("KNOCKOUT", 1),
        ("FINAL", 1),
        ("GROUPS", 1), ("GROUPS", 2),
    ]
    for i, (phase, md) in enumerate(phases, start=1):
        s.add(Event(
            e_block_id=i, schema_id=10, product="football",
            phase=phase, match_day=md,
            event_time=f"2026-05-09T1{i}:00:00Z", captured_ts=1.0,
        ))
    s.flush()

    new = detect_seasons(s, require_standings_reset=False)
    assert new == 2

    seasons = sorted(s.scalars(select(Season)).all(), key=lambda x: x.season_index)
    assert [x.season_index for x in seasons] == [1, 2]

    by_eb = {e.e_block_id: e for e in s.scalars(select(Event))}
    assert by_eb[4].season_id == seasons[0].season_id
    assert by_eb[5].season_id == seasons[1].season_id
    s.close()


def test_tournament_unknown_phase_ignored(tmp_path: Path) -> None:
    s = _session(tmp_path)
    _seed_schema(s, schema_id=11, kind="tournament")
    s.add(Event(e_block_id=1, schema_id=11, product="football",
                phase="GROUPS", match_day=1, event_time="2026-05-09T10:00:00Z",
                captured_ts=1.0))
    s.add(Event(e_block_id=2, schema_id=11, product="football",
                phase="WEIRD", match_day=1, event_time="2026-05-09T11:00:00Z",
                captured_ts=1.0))
    s.add(Event(e_block_id=3, schema_id=11, product="football",
                phase="KNOCKOUT", match_day=1, event_time="2026-05-09T12:00:00Z",
                captured_ts=1.0))
    s.flush()

    new = detect_seasons(s, require_standings_reset=False)
    assert new == 1
    s.close()


def test_idempotent_when_re_run(tmp_path: Path) -> None:
    s = _session(tmp_path)
    _seed_schema(s)
    for i, md in enumerate([1, 2, 3], start=1):
        _seed_event(s, e_block_id=i, schema_id=1, match_day=md, event_time=f"2026-05-09T1{i}:00:00Z")
    s.flush()

    detect_seasons(s, require_standings_reset=False)
    s.commit()
    detect_seasons(s, require_standings_reset=False)
    s.commit()
    assert len(s.scalars(select(Season)).all()) == 1
    s.close()
