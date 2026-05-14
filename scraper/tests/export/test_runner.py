"""End-to-end export tests: seed a small DB, run `run_export`, assert the
files on disk have the expected shape."""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

import pytest

from src.config import Config, Database, Paths
from src.db import init_db, make_engine, make_session_factory
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
)
from src.export import run_export


@pytest.fixture
def populated_session(tmp_path: Path):
    cfg = Config(
        paths=Paths(data_dir=tmp_path),
        database=Database(url=f"sqlite:///{tmp_path / 'e.db'}"),
    )
    init_db(config=cfg)
    Session = make_session_factory(make_engine(cfg))
    s = Session()

    # --- Football: one league with one season + one matchday snapshot ---
    s.add(Schema(schema_id=41104, product="football", kind="league",
                 description="England 2026", num_participants=20,
                 first_seen_ts=0.0, last_seen_ts=0.0))
    s.add(SchemaParticipant(schema_id=41104, team_id="501", name="PSG", fifa_code="PSG", stars=5.0))
    s.add(SchemaParticipant(schema_id=41104, team_id="336", name="Liverpool", fifa_code="LIV", stars=4.5))
    s.add(SchemaMarket(schema_id=41104, slot=0, market_id="m1",
                       market_name="Match Result", odd_id="o1", odd_name="Home"))
    s.add(SchemaMarket(schema_id=41104, slot=1, market_id="m1",
                       market_name="Match Result", odd_id="o2", odd_name="Away"))
    s.add(SchemaMarket(schema_id=41104, slot=2, market_id="m1",
                       market_name="Match Result", odd_id="o3", odd_name="Draw"))
    season = Season(schema_id=41104, season_index=1, started_at="2026-05-09T10:00:00Z")
    s.add(season)
    s.flush()
    s.add(Event(e_block_id=12345, schema_id=41104, season_id=season.season_id,
                product="football", server_status="FINISHED",
                event_time="2026-05-09T16:00:00Z",
                captured_ts=time.time(), settled_ts=time.time() + 60,
                home_team_id="501", away_team_id="336",
                home_score=2, away_score=1,
                match_day=1, phase="GROUPS",
                won_markets="Match_Result_Home,_2_1"))
    s.add(EventParticipantFootball(e_block_id=12345, side="home", team_id="501", stars=5.0))
    s.add(EventParticipantFootball(e_block_id=12345, side="away", team_id="336", stars=4.5))
    s.add(Odds(e_block_id=12345, slot=0, odds=1.85))
    s.add(Odds(e_block_id=12345, slot=1, odds=4.20))
    s.add(Odds(e_block_id=12345, slot=2, odds=3.40))
    s.add(MatchDaySnapshot(
        season_id=season.season_id, phase="GROUPS", match_day=1,
        finalized_ts=time.time(),
        matches_json={"matches": [{"e_block_id": 12345}]},
        standings_json={"standings": [{"rank": 1, "team_id": "501", "points": 3}]},
        summary_json={"matches_count": 1, "total_goals": 3},
    ))

    # --- Dogs: one schema with one race ---
    s.add(Schema(schema_id=20100, product="dogs", kind="unknown",
                 description="Dogs UK 480m", num_participants=6,
                 first_seen_ts=0.0, last_seen_ts=0.0))
    s.add(Event(e_block_id=99999, schema_id=20100, product="dogs",
                server_status="FINISHED",
                event_time="2026-05-09T15:30:00Z",
                captured_ts=time.time(), settled_ts=time.time() + 30,
                num_runners=3, winner_id="d2", second_id="d1", third_id="d3",
                final_order="d2,d1,d3",
                surface="sand", distance=480.0, weather="clear",
                won_markets="Win_d2"))
    for trap, rid, name in [(1, "d1", "Lightning"), (2, "d2", "Thunder"), (3, "d3", "Bolt")]:
        s.add(EventRunner(e_block_id=99999, runner_id=rid, trap=trap, name=name))
    s.commit()
    yield s, tmp_path
    s.close()


def test_export_writes_full_tree(populated_session, tmp_path) -> None:
    session, _ = populated_session
    out = tmp_path / "exports"
    stats = run_export(session, out)

    assert stats.schemas == 2
    assert stats.seasons == 1
    assert stats.matchdays == 1
    assert stats.races == 1
    assert "football" in stats.products_written
    assert "dogs" in stats.products_written

    # manifest at root
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["counts"]["schemas"] == 2
    assert manifest["scraper_version"]

    # football tree
    fb_schema_dir = out / "football" / "schemas" / "41104_england-2026"
    assert (fb_schema_dir / "schema.json").is_file()
    schema_doc = json.loads((fb_schema_dir / "schema.json").read_text())
    assert schema_doc["product"] == "football"
    assert {p["team_id"] for p in schema_doc["participants"]} == {"501", "336"}

    md_path = fb_schema_dir / "seasons" / "1" / "GROUPS" / "matchday-01.json"
    assert md_path.is_file()
    md = json.loads(md_path.read_text())
    assert md["match_day"] == 1
    assert md["phase"] == "GROUPS"
    match = md["matches"][0]
    assert match["home"]["team_id"] == "501"
    assert match["home"]["score"] == 2
    # odds humanized
    assert "Match Result.Home" in match["odds"]
    assert match["odds"]["Match Result.Home"] == 1.85
    # won_markets parsed where possible, raw kept either way
    raws = [w["raw"] for w in match["won_markets"]]
    assert "Match_Result_Home" in raws
    assert any(w.get("market") == "Match Result" for w in match["won_markets"])

    # NDJSON rollups
    fb_rollup = (out / "football" / "all-events.ndjson").read_text().splitlines()
    assert len(fb_rollup) == 1
    row = json.loads(fb_rollup[0])
    assert row["e_block_id"] == 12345
    assert row["home_team_id"] == "501"
    assert "Match Result.Home" in row["odds"]

    # dogs tree
    dog_schema_dir = out / "dogs" / "schemas" / "20100_dogs-uk-480m"
    race_path = dog_schema_dir / "races" / "2026-05-09" / "race-99999.json"
    assert race_path.is_file()
    race = json.loads(race_path.read_text())
    assert race["product"] == "dogs"
    assert race["track"]["distance"] == 480.0
    assert race["result"]["winner"]["runner_id"] == "d2"
    assert race["result"]["winner"]["trap"] == 2
    assert race["result"]["final_order"] == ["d2", "d1", "d3"]


def test_export_partial_season_marker(populated_session, tmp_path) -> None:
    """Seasons with ended_at == NULL should be flagged `partial: true`."""
    session, _ = populated_session
    out = tmp_path / "exports2"
    run_export(session, out)
    season_doc = json.loads((out / "football" / "schemas" / "41104_england-2026"
                             / "seasons" / "1" / "season.json").read_text())
    assert season_doc["partial"] is True


def test_export_filter_by_product(populated_session, tmp_path) -> None:
    session, _ = populated_session
    out = tmp_path / "exports3"
    stats = run_export(session, out, product="dogs")
    assert stats.products_written == ["dogs"]
    assert not (out / "football").exists()
    assert (out / "dogs").exists()


def test_export_bundle_zips_the_tree(populated_session, tmp_path) -> None:
    session, _ = populated_session
    out = tmp_path / "exports4"
    stats = run_export(session, out, bundle=True)
    zip_path = out.with_suffix(".zip")
    assert stats.output_dir == zip_path
    assert zip_path.is_file()
    assert not out.exists()  # original directory cleaned up
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert any("manifest.json" in n for n in names)
        assert any("matchday-01.json" in n for n in names)


def test_export_filter_by_schema(populated_session, tmp_path) -> None:
    session, _ = populated_session
    out = tmp_path / "exports5"
    stats = run_export(session, out, schema_id=20100)
    # Both products are walked but only the matching schema appears under each
    assert stats.schemas == 1
    assert (out / "dogs" / "schemas" / "20100_dogs-uk-480m").exists()
    # No football schemas should have been written even though the football
    # branch was visited; the schema filter excluded all of them
    fb_schemas = list((out / "football" / "schemas").glob("*"))
    assert fb_schemas == []
