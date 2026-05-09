"""Smoke + behaviour tests for the per-race-product routes (Dogs, Horses,
Speedway, Motorbikes, MMA). Mostly verifies that the factory wired all five
groups correctly and that each shape responds with the expected fields."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.config import Config, Database, Paths
from src.db import init_db, make_engine, make_session_factory
from src.db.models import Event, EventRunner, Schema


@pytest.fixture
def race_client(tmp_path: Path):
    cfg = Config(
        paths=Paths(data_dir=tmp_path, captures_dir=tmp_path / "captures"),
        database=Database(url=f"sqlite:///{tmp_path / 'race.db'}"),
    )
    init_db(config=cfg)
    engine = make_engine(cfg)
    Session = make_session_factory(engine)
    with Session() as s:
        # one dog schema with two events
        s.add(Schema(schema_id=20100, product="dogs", kind="unknown",
                     description="Dogs UK 480m", num_participants=6,
                     countdown_s=60, first_seen_ts=0.0, last_seen_ts=0.0))
        # event 1: trap 3 wins
        s.add(Event(e_block_id=1001, schema_id=20100, product="dogs",
                    server_status="FINISHED", event_time="2026-05-09T10:00:00Z",
                    captured_ts=time.time(), settled_ts=time.time() + 60,
                    num_runners=6, winner_id="d3", second_id="d1", third_id="d2",
                    final_order="d3,d1,d2,d4,d5,d6",
                    surface="sand", distance=480.0, weather="clear"))
        for trap, runner_id, name in [
            (1, "d1", "Lightning"), (2, "d2", "Thunder"),
            (3, "d3", "Bolt"),      (4, "d4", "Dasher"),
            (5, "d5", "Comet"),     (6, "d6", "Streak"),
        ]:
            s.add(EventRunner(e_block_id=1001, runner_id=runner_id, trap=trap, name=name))
        # event 2: trap 1 wins (same dog d1)
        s.add(Event(e_block_id=1002, schema_id=20100, product="dogs",
                    server_status="FINISHED", event_time="2026-05-09T10:05:00Z",
                    captured_ts=time.time(), settled_ts=time.time() + 60,
                    num_runners=6, winner_id="d1", second_id="d3", third_id="d2",
                    final_order="d1,d3,d2,d4,d5,d6"))
        for trap, runner_id, name in [
            (1, "d1", "Lightning"), (2, "d2", "Thunder"),
            (3, "d3", "Bolt"),      (4, "d4", "Dasher"),
            (5, "d5", "Comet"),     (6, "d6", "Streak"),
        ]:
            s.add(EventRunner(e_block_id=1002, runner_id=runner_id, trap=trap, name=name))

        # one horse schema with no events (just so /horses/schemas isn't empty)
        s.add(Schema(schema_id=21100, product="horses", kind="unknown",
                     description="Horses UK Flat 1m", num_participants=10,
                     first_seen_ts=0.0, last_seen_ts=0.0))
        s.commit()

    return TestClient(create_app(cfg))


def test_dogs_schemas_lists_only_dogs(race_client) -> None:
    r = race_client.get("/dogs/schemas")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["schemas"][0]["schema_id"] == 20100
    assert body["schemas"][0]["events"] == 2


def test_horses_schemas_does_not_include_dogs(race_client) -> None:
    r = race_client.get("/horses/schemas")
    assert r.status_code == 200
    ids = [s["schema_id"] for s in r.json()["schemas"]]
    assert ids == [21100]
    assert 20100 not in ids


def test_dogs_schema_detail(race_client) -> None:
    r = race_client.get("/dogs/schemas/20100")
    assert r.status_code == 200
    body = r.json()
    assert body["product"] == "dogs"
    assert body["events"] == 2
    assert body["first_event_captured_ts"] is not None


def test_dogs_schema_detail_404_for_horse_schema(race_client) -> None:
    """Asking for a horses schema under /dogs returns 404, not 200."""
    r = race_client.get("/dogs/schemas/21100")
    assert r.status_code == 404


def test_dogs_schema_events(race_client) -> None:
    r = race_client.get("/dogs/schemas/20100/events?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    by_id = {e["e_block_id"]: e for e in body["events"]}
    assert by_id[1001]["winner_id"] == "d3"
    assert by_id[1001]["final_order"] == ["d3", "d1", "d2", "d4", "d5", "d6"]
    assert by_id[1001]["distance"] == 480.0


def test_dogs_winners_by_trap(race_client) -> None:
    r = race_client.get("/dogs/schemas/20100/winners-by-trap")
    assert r.status_code == 200
    body = r.json()
    assert body["total_settled_events"] == 2
    by_trap = {row["trap"]: row for row in body["winners_by_trap"]}
    # trap 1 (d1) won once, trap 3 (d3) won once
    assert by_trap[1]["wins"] == 1
    assert by_trap[3]["wins"] == 1
    assert abs(by_trap[1]["win_rate"] - 0.5) < 1e-9


def test_dogs_top_runners(race_client) -> None:
    r = race_client.get("/dogs/runners/top")
    assert r.status_code == 200
    body = r.json()
    assert body["product"] == "dogs"
    runners_by_id = {r["runner_id"]: r for r in body["runners"]}
    # d1 and d3 each won once
    assert runners_by_id["d1"]["wins"] == 1
    assert runners_by_id["d3"]["wins"] == 1
    assert runners_by_id["d1"]["name"] == "Lightning"


def test_top_runners_filters_by_schema(race_client) -> None:
    r = race_client.get("/dogs/runners/top?schema_id=99999")
    assert r.status_code == 200
    assert r.json()["count"] == 0  # unknown schema, no winners


@pytest.mark.parametrize("product", ["horses", "speedway", "motorbikes", "mma"])
def test_other_products_have_routes_mounted(race_client, product) -> None:
    """Every race product the factory was instantiated for must respond on
    the canonical endpoints (even with empty data)."""
    r = race_client.get(f"/{product}/schemas")
    assert r.status_code == 200
    r = race_client.get(f"/{product}/runners/top")
    assert r.status_code == 200


def test_openapi_groups_each_race_product(race_client) -> None:
    r = race_client.get("/openapi.json")
    assert r.status_code == 200
    tags = {t["name"] for t in r.json().get("tags", [])}
    for tag in ("Dogs", "Horses", "Speedway", "Motorbikes", "MMA"):
        assert tag in tags
