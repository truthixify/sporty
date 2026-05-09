from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.config import Config, Database, Paths
from src.db import init_db, make_engine, make_session_factory
from src.db.models import (
    Event,
    MatchDaySnapshot,
    Schema,
    Season,
    Standing,
)


@pytest.fixture
def app_client(tmp_path: Path):
    cfg = Config(
        paths=Paths(data_dir=tmp_path, captures_dir=tmp_path / "captures"),
        database=Database(url=f"sqlite:///{tmp_path / 'api.db'}"),
    )
    init_db(config=cfg)
    engine = make_engine(cfg)
    Session = make_session_factory(engine)
    with Session() as s:
        s.add(Schema(schema_id=41104, product="football", kind="league",
                     description="Test League", first_seen_ts=0.0, last_seen_ts=0.0))
        season = Season(schema_id=41104, season_index=1, started_at="2026-05-09T10:00:00Z",
                        matchdays_completed=1)
        s.add(season)
        s.flush()
        s.add(Event(e_block_id=1, schema_id=41104, season_id=season.season_id,
                    product="football", server_status="FINISHED", event_time="2026-05-09T10:01:00Z",
                    captured_ts=time.time(), settled_ts=time.time(),
                    home_team_id="A", away_team_id="B", home_score=2, away_score=1, match_day=1))
        s.add(Standing(e_block_id=1, team_id="A", season_id=season.season_id,
                       match_day=1, ranking=1, points=3))
        s.add(MatchDaySnapshot(
            season_id=season.season_id, match_day=1, finalized_ts=time.time(),
            matches_json={"matches": [{"e_block_id": 1, "home_team_id": "A", "away_team_id": "B"}]},
            standings_json={"standings": [{"team_id": "A", "ranking": 1, "points": 3}]},
            summary_json={"matches_count": 1, "total_goals": 3},
        ))
        s.commit()
        season_id = season.season_id

    app = create_app(cfg)
    return TestClient(app), season_id


def test_health(app_client) -> None:
    client, _ = app_client
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_ready_returns_payload(app_client) -> None:
    client, _ = app_client
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body and "last_event_age_s" in body


def test_metrics_capture(app_client) -> None:
    client, _ = app_client
    r = client.get("/metrics/capture")
    assert r.status_code == 200
    body = r.json()
    assert body["events_per_hour"] >= 1.0
    assert body["last_event_ts"] is not None


def test_db_stats_includes_all_tables(app_client) -> None:
    client, _ = app_client
    r = client.get("/db/stats")
    assert r.status_code == 200
    counts = r.json()["row_counts"]
    assert counts["events"] == 1
    assert counts["schemas"] == 1
    assert counts["seasons"] == 1
    assert counts["match_day_snapshots"] == 1
    assert "alembic_version" not in counts


def test_events_recent_and_detail(app_client) -> None:
    client, _ = app_client
    r = client.get("/events/recent")
    assert r.status_code == 200 and r.json()["count"] == 1
    payload = r.json()["events"][0]
    # Football summary should expose football fields and server_status
    assert payload["product"] == "football"
    assert payload["server_status"] == "FINISHED"
    assert payload["home_score"] == 2
    assert payload["away_score"] == 1
    assert "winner_id" not in payload
    assert "num_runners" not in payload

    r = client.get("/events/1")
    assert r.status_code == 200
    body = r.json()
    assert body["e_block_id"] == 1
    assert body["home_score"] == 2
    assert any(s["team_id"] == "A" for s in body["standings"])


def test_events_recent_race_summary_excludes_football_fields(tmp_path, app_client) -> None:
    from sqlalchemy import select

    from src.api import deps
    from src.db.models import Event, Schema

    client, _ = app_client
    # Add a race event into the same DB the app is bound to
    factory = deps._factory
    assert factory is not None
    with factory() as s:
        s.add(Schema(schema_id=20100, product="dogs", kind="unknown",
                     first_seen_ts=0, last_seen_ts=0))
        s.add(Event(
            e_block_id=999, schema_id=20100, product="dogs",
            server_status="FINISHED",
            event_time="2026-05-09T20:00:00Z",
            captured_ts=__import__("time").time() + 1,
            num_runners=6, winner_id="d3", second_id="d1",
        ))
        s.commit()

    r = client.get("/events?product=dogs")
    assert r.status_code == 200
    rows = r.json()["events"]
    assert any(e["e_block_id"] == 999 for e in rows)
    race = next(e for e in rows if e["e_block_id"] == 999)
    assert race["winner_id"] == "d3"
    assert race["num_runners"] == 6
    assert "home_score" not in race
    assert "match_day" not in race


def test_event_not_found(app_client) -> None:
    client, _ = app_client
    r = client.get("/events/9999")
    assert r.status_code == 404


def test_football_schemas_endpoint(app_client) -> None:
    client, _ = app_client
    r = client.get("/football/schemas")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["schemas"][0]["schema_id"] == 41104
    assert body["schemas"][0]["seasons"] == 1


def test_matchday_view(app_client) -> None:
    client, season_id = app_client
    r = client.get(f"/football/seasons/{season_id}/matchdays/1")
    assert r.status_code == 200
    body = r.json()
    assert body["match_day"] == 1
    assert body["matches"][0]["e_block_id"] == 1
    assert body["standings"][0]["team_id"] == "A"
    assert body["summary"]["matches_count"] == 1


def test_season_summary(app_client) -> None:
    client, season_id = app_client
    r = client.get(f"/football/seasons/{season_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["season_id"] == season_id
    assert body["matchdays"] == [1]


def test_final_standings(app_client) -> None:
    client, season_id = app_client
    r = client.get(f"/football/seasons/{season_id}/standings/final")
    assert r.status_code == 200
    body = r.json()
    assert body["match_day"] == 1
    assert body["standings"][0]["team_id"] == "A"
