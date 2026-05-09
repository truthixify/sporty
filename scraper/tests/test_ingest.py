from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from src.config import Config, Database, Paths
from src.db import init_db, make_engine, make_session_factory
from src.db.models import (
    Event,
    EventRunner,
    Odds,
    Schema,
    Standing,
)
from src.parse import ingest_journal, ingest_journals


def _frame_out(xs: int, resource: str) -> dict:
    return {
        "t": float(xs),
        "dir": "out",
        "data": json.dumps({
            "type": "REQUEST", "xs": xs,
            "req": {"method": "GET", "resource": resource},
        }),
    }


def _frame_in(xs: int, body: object, status: int = 200) -> dict:
    return {
        "t": float(xs) + 0.1,
        "dir": "in",
        "data": json.dumps({
            "type": "RESPONSE", "xs": xs,
            "res": {"statusCode": status, "body": body},
        }),
    }


def _football_schema_template() -> dict:
    return {
        "id": 41104,
        "description": "Test League",
        "filter": {"competitionType": "LEAGUE", "numParticipants": 20, "isTwoLegsGroup": True},
        "schedulerConfiguration": [{"dailySchedule": [{"countdown": 180}]}],
        "participantTemplates": [
            {"classType": "FbParticipant", "id": "501", "name": "PSG", "fifaCode": "PSG", "stars": 5.0},
            {"classType": "FbParticipant", "id": "336", "name": "Liverpool", "fifaCode": "LIV", "stars": 4.5},
        ],
        "marketTemplates": [{"id": "m1", "name": "Match", "odds": [
            {"id": "o1", "name": "Home", "value": 0},
            {"id": "o2", "name": "Away", "value": 1},
        ]}],
    }


def _football_event_block(with_result: bool = False) -> dict:
    block = {
        "eBlockId": 12345, "playlistId": 41104,
        "serverStatus": "FINISHED" if with_result else "SCHEDULED",
        "eventTime": "2026-05-09T16:00:00Z",
        "data": {"classType": "FbEventBlockData", "matchDay": 17, "phase": "GROUPS"},
        "events": [{
            "data": {
                "participants": [
                    {"classType": "FbParticipant", "id": "501", "stars": 5.0},
                    {"classType": "FbParticipant", "id": "336", "stars": 4.5},
                ],
                "oddValues": ["1.50", "5.00"],
            }
        }],
    }
    if with_result:
        block["events"][0]["result"] = {"finalOutcome": ["2", "0"], "wonMarkets": ["Match_Home"]}
    return block


def _dogs_event_block(with_result: bool = False) -> dict:
    block = {
        "eBlockId": 99999, "playlistId": 71001,
        "serverStatus": "FINISHED" if with_result else "SCHEDULED",
        "eventTime": "2026-05-09T16:01:00Z",
        "events": [{
            "data": {
                "participants": [
                    {"classType": "DogParticipant", "id": "d1", "name": "Lightning"},
                    {"classType": "DogParticipant", "id": "d2", "name": "Thunder"},
                ],
                "gameData": {"distance": 480.0, "surface": "sand"},
                "oddValues": ["2.00", "3.50"],
            }
        }],
    }
    if with_result:
        block["events"][0]["result"] = {"data": {"finalOrder": ["d2", "d1"]}, "wonMarkets": ["W_d2"]}
    return block


def _make_journal(path: Path) -> None:
    records = [
        {"t": 0.5, "kind": "open", "url": "wss://x"},
        _frame_out(1, "/api/client/v0.1/playlists/all"),
        _frame_in(1, [_football_schema_template()]),
        _frame_out(2, "/eventBlocks/event/data"),
        _frame_in(2, [_football_event_block()]),
        _frame_out(3, "/eventBlocks/event/data"),
        _frame_in(3, [_dogs_event_block()]),
        _frame_out(4, "/eventBlocks/event/result"),
        _frame_in(4, [_football_event_block(with_result=True)]),
        _frame_out(5, "/eventBlocks/event/result"),
        _frame_in(5, [_dogs_event_block(with_result=True)]),
        {"t": 99.0, "kind": "close", "url": "wss://x"},
    ]
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _make_session(tmp_path: Path):
    cfg = Config(paths=Paths(data_dir=tmp_path), database=Database(url=f"sqlite:///{tmp_path / 'scraper.db'}"))
    init_db(config=cfg)
    engine = make_engine(cfg)
    Session = make_session_factory(engine)
    return Session(), cfg


def test_ingest_synthetic_journal_populates_db(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    _make_journal(journal)
    session, _ = _make_session(tmp_path)

    stats = ingest_journal(journal, session)
    session.commit()

    assert stats.schemas == 1
    assert stats.events >= 4

    schemas = {s.schema_id: s for s in session.scalars(select(Schema)).all()}
    assert set(schemas) == {41104, 71001}
    assert schemas[41104].kind == "league"
    assert schemas[71001].kind == "unknown"
    assert schemas[71001].product == "dogs"

    events = session.scalars(select(Event)).all()
    by_id = {e.e_block_id: e for e in events}
    assert by_id[12345].home_score == 2
    assert by_id[12345].away_score == 0
    assert by_id[12345].home_team_id == "501"
    assert by_id[99999].winner_id == "d2"
    assert by_id[99999].num_runners == 2

    odds = session.scalars(select(Odds)).all()
    assert len(odds) == 4

    runners = session.scalars(select(EventRunner)).all()
    assert {(r.e_block_id, r.runner_id) for r in runners} == {(99999, "d1"), (99999, "d2")}

    session.close()


def test_ingest_is_idempotent(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    _make_journal(journal)
    session, _ = _make_session(tmp_path)

    ingest_journal(journal, session)
    session.commit()
    first_event_count = len(session.scalars(select(Event)).all())

    ingest_journal(journal, session)
    session.commit()
    second_event_count = len(session.scalars(select(Event)).all())

    assert first_event_count == second_event_count
    session.close()


def test_ingest_journals_handles_multiple_files(tmp_path: Path) -> None:
    j1 = tmp_path / "j1.jsonl"
    j2 = tmp_path / "j2.jsonl"
    _make_journal(j1)
    _make_journal(j2)
    session, _ = _make_session(tmp_path)

    stats = ingest_journals([j1, j2], session)
    session.commit()

    assert stats.files == 2
    events = session.scalars(select(Event)).all()
    assert len(events) == 2
    session.close()
