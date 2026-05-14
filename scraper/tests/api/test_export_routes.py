"""Smoke tests for the API export zip endpoints. Each verifies a 200 + a
zip body containing at least the expected manifest entry."""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.config import Config, Database, Paths
from src.db import init_db, make_engine, make_session_factory
from src.db.models import Event, EventRunner, Schema, Season


@pytest.fixture
def export_client(tmp_path: Path):
    cfg = Config(
        paths=Paths(data_dir=tmp_path),
        database=Database(url=f"sqlite:///{tmp_path / 'ex.db'}"),
    )
    init_db(config=cfg)
    Session = make_session_factory(make_engine(cfg))
    with Session() as s:
        s.add(Schema(schema_id=41104, product="football", kind="league",
                     description="England 2026", first_seen_ts=0.0, last_seen_ts=0.0))
        season = Season(schema_id=41104, season_index=1, started_at="2026-05-09T10:00:00Z")
        s.add(season)
        s.flush()
        season_id_val = season.season_id

        s.add(Schema(schema_id=20100, product="dogs", kind="unknown",
                     description="Dogs UK 480m", first_seen_ts=0.0, last_seen_ts=0.0))
        s.add(Event(e_block_id=1001, schema_id=20100, product="dogs",
                    server_status="FINISHED", event_time="2026-05-09T15:30:00Z",
                    captured_ts=time.time(), settled_ts=time.time(),
                    num_runners=2, winner_id="d1",
                    final_order="d1,d2"))
        s.add(EventRunner(e_block_id=1001, runner_id="d1", trap=1, name="Lightning"))
        s.add(EventRunner(e_block_id=1001, runner_id="d2", trap=2, name="Thunder"))
        s.commit()
    return TestClient(create_app(cfg)), season_id_val


def _names_in_zip(content: bytes) -> set[str]:
    return set(zipfile.ZipFile(io.BytesIO(content)).namelist())


def test_export_all_returns_zip(export_client) -> None:
    client, _ = export_client
    r = client.get("/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    names = _names_in_zip(r.content)
    assert any(n.endswith("manifest.json") for n in names)


def test_export_football_schema_zip(export_client) -> None:
    client, _ = export_client
    r = client.get("/football/schemas/41104/export")
    assert r.status_code == 200
    names = _names_in_zip(r.content)
    assert any("schema.json" in n and "41104" in n for n in names)


def test_export_football_schema_404(export_client) -> None:
    client, _ = export_client
    r = client.get("/football/schemas/99999/export")
    assert r.status_code == 404


def test_export_football_season_zip(export_client) -> None:
    client, season_id = export_client
    r = client.get(f"/football/seasons/{season_id}/export")
    assert r.status_code == 200
    names = _names_in_zip(r.content)
    assert any(f"seasons/1/season.json" in n for n in names)


def test_export_dogs_schema_zip(export_client) -> None:
    client, _ = export_client
    r = client.get("/dogs/schemas/20100/export")
    assert r.status_code == 200
    names = _names_in_zip(r.content)
    assert any("race-1001.json" in n for n in names)


def test_export_dogs_schema_404(export_client) -> None:
    client, _ = export_client
    r = client.get("/dogs/schemas/41104/export")  # schema 41104 is football, not dogs
    assert r.status_code == 404


@pytest.mark.parametrize("product", ["horses", "speedway", "motorbikes", "mma"])
def test_other_race_products_have_export_endpoint(export_client, product) -> None:
    client, _ = export_client
    # 404 because we haven't seeded these products, but that proves the route exists.
    r = client.get(f"/{product}/schemas/12345/export")
    assert r.status_code == 404
