from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from src.config import Config, Database, Paths
from src.db import init_db, make_engine, make_session_factory
from src.db.models import Event, MatchDaySnapshot, Schema, Season, Standing
from src.parse.matchday_aggregator import aggregate_matchdays


def _session(tmp_path: Path):
    cfg = Config(paths=Paths(data_dir=tmp_path), database=Database(url=f"sqlite:///{tmp_path / 'a.db'}"))
    init_db(config=cfg)
    engine = make_engine(cfg)
    return make_session_factory(engine)()


def _seed_basics(s) -> int:
    s.add(Schema(schema_id=1, product="football", kind="league", first_seen_ts=0, last_seen_ts=0))
    season = Season(schema_id=1, season_index=1, started_at="2026-05-09T10:00:00Z")
    s.add(season)
    s.flush()
    return season.season_id


def test_aggregator_builds_snapshot_when_all_events_settled(tmp_path: Path) -> None:
    s = _session(tmp_path)
    season_id = _seed_basics(s)

    s.add(Event(e_block_id=10, schema_id=1, season_id=season_id, product="football",
                match_day=1, event_time="2026-05-09T10:01:00Z",
                captured_ts=1.0, settled_ts=2.0,
                home_team_id="A", away_team_id="B", home_score=3, away_score=1,
                won_markets="Home,Over_2_5"))
    s.add(Event(e_block_id=11, schema_id=1, season_id=season_id, product="football",
                match_day=1, event_time="2026-05-09T10:02:00Z",
                captured_ts=1.0, settled_ts=2.5,
                home_team_id="C", away_team_id="D", home_score=0, away_score=2))
    s.add(Standing(e_block_id=11, team_id="A", season_id=season_id, match_day=1,
                   ranking=1, points=3, wins=1, draws=0, losses=0,
                   goals_for=3, goals_against=1, goal_diff=2))
    s.add(Standing(e_block_id=11, team_id="D", season_id=season_id, match_day=1,
                   ranking=2, points=3, wins=1, draws=0, losses=0,
                   goals_for=2, goals_against=0, goal_diff=2))
    s.flush()

    written = aggregate_matchdays(s)
    s.commit()
    assert written == 1

    snap = s.get(MatchDaySnapshot, (season_id, 1))
    assert snap is not None
    assert len(snap.matches_json["matches"]) == 2
    assert {m["e_block_id"] for m in snap.matches_json["matches"]} == {10, 11}
    assert snap.summary_json["matches_count"] == 2
    assert snap.summary_json["home_wins"] == 1
    assert snap.summary_json["away_wins"] == 1
    assert snap.summary_json["draws"] == 0
    assert snap.summary_json["total_goals"] == 6
    assert snap.summary_json["biggest_win"]["e_block_id"] in (10, 11)
    assert {st["team_id"] for st in snap.standings_json["standings"]} == {"A", "D"}
    s.close()


def test_aggregator_skips_unfinished_matchday(tmp_path: Path) -> None:
    s = _session(tmp_path)
    season_id = _seed_basics(s)

    s.add(Event(e_block_id=20, schema_id=1, season_id=season_id, product="football",
                match_day=2, event_time="2026-05-09T11:00:00Z",
                captured_ts=1.0, settled_ts=2.0))
    s.add(Event(e_block_id=21, schema_id=1, season_id=season_id, product="football",
                match_day=2, event_time="2026-05-09T11:01:00Z",
                captured_ts=1.0, settled_ts=None))
    s.flush()

    written = aggregate_matchdays(s)
    assert written == 0
    assert s.get(MatchDaySnapshot, (season_id, 2)) is None
    s.close()


def test_aggregator_idempotent(tmp_path: Path) -> None:
    s = _session(tmp_path)
    season_id = _seed_basics(s)
    s.add(Event(e_block_id=30, schema_id=1, season_id=season_id, product="football",
                match_day=3, event_time="2026-05-09T12:00:00Z",
                captured_ts=1.0, settled_ts=2.0,
                home_team_id="X", away_team_id="Y", home_score=1, away_score=1))
    s.flush()
    aggregate_matchdays(s)
    s.commit()
    aggregate_matchdays(s)
    s.commit()
    snaps = s.scalars(select(MatchDaySnapshot)).all()
    assert len(snaps) == 1
    s.close()
