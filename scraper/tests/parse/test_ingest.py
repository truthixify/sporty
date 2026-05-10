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

    stats, _ = ingest_journal(journal, session)
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

    ingest_journal(journal, session)  # type: ignore[func-returns-value]
    session.commit()
    second_event_count = len(session.scalars(select(Event)).all())

    assert first_event_count == second_event_count
    session.close()


def test_incremental_mode_resumes_from_watermark(tmp_path: Path) -> None:
    from src.db.models import ParseWatermark

    journal = tmp_path / "j.jsonl"
    _make_journal(journal)
    session, _ = _make_session(tmp_path)

    stats1 = ingest_journals([journal], session, incremental=True)
    session.commit()
    assert stats1.frames > 0
    # Watermark uses the resolved absolute path, not whatever string the caller passed
    wm = session.get(ParseWatermark, str(journal.resolve()))
    assert wm is not None
    assert wm.last_offset == journal.stat().st_size

    # Second incremental run with no new lines should consume zero frames
    stats2 = ingest_journals([journal], session, incremental=True)
    session.commit()
    assert stats2.frames == 0
    session.close()


def test_incremental_picks_up_appended_lines(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    _make_journal(journal)
    session, _ = _make_session(tmp_path)

    ingest_journals([journal], session, incremental=True)
    session.commit()

    # Append a couple more synthetic frames
    extra_block = _football_event_block(with_result=True)
    extra_block["eBlockId"] = 22222
    with journal.open("a") as f:
        f.write(json.dumps(_frame_out(99, "/eventBlocks/event/result")) + "\n")
        f.write(json.dumps(_frame_in(99, [extra_block])) + "\n")

    stats = ingest_journals([journal], session, incremental=True)
    session.commit()
    assert stats.frames == 2
    events = {e.e_block_id for e in session.scalars(select(Event)).all()}
    assert 22222 in events
    session.close()


def test_legacy_relative_path_watermark_is_auto_migrated(tmp_path: Path) -> None:
    """If the DB has a leftover relative-path watermark for a file we now
    address by absolute path, the next parse run should drop the alias."""
    from src.db.models import ParseWatermark

    journal = tmp_path / "j.jsonl"
    _make_journal(journal)
    session, _ = _make_session(tmp_path)

    # Seed a stale relative-path watermark for the same file
    session.add(ParseWatermark(
        journal_file="some/relative/path/that/aliases.jsonl",  # bogus key, won't resolve
        last_offset=999, last_line=999, parsed_count=999, last_run_ts=1.0,
    ))
    # And a valid alias: the resolved version of the same file via a relative path
    session.add(ParseWatermark(
        journal_file=str(journal.relative_to(tmp_path)),  # 'j.jsonl' (relative)
        last_offset=0, last_line=0, parsed_count=0, last_run_ts=1.0,
    ))
    session.commit()

    import os
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        ingest_journals([Path("j.jsonl")], session, incremental=True)
        session.commit()
    finally:
        os.chdir(cwd)

    wms = session.scalars(select(ParseWatermark)).all()
    keys = sorted(w.journal_file for w in wms)
    # The aliased relative key 'j.jsonl' should be gone; the bogus one stays.
    assert "j.jsonl" not in keys
    assert any(k.endswith("/j.jsonl") and k.startswith("/") for k in keys)
    session.close()


def test_relative_and_absolute_paths_share_a_watermark(tmp_path: Path, monkeypatch) -> None:
    """The same file referenced via different relative paths from different
    cwds must collapse to one watermark row, not duplicate."""
    from src.db.models import ParseWatermark

    journal = tmp_path / "j.jsonl"
    _make_journal(journal)
    session, _ = _make_session(tmp_path)

    # First call: pass the absolute path
    ingest_journals([journal], session, incremental=True)
    session.commit()
    # Second call: pass a relative path while cwd is the tmp_path
    monkeypatch.chdir(tmp_path)
    ingest_journals([Path("j.jsonl")], session, incremental=True)
    session.commit()

    wms = session.scalars(select(ParseWatermark)).all()
    assert len(wms) == 1  # collapsed
    session.close()


def test_backfill_ignores_existing_watermark(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    _make_journal(journal)
    session, _ = _make_session(tmp_path)

    ingest_journals([journal], session, incremental=True)
    session.commit()

    stats = ingest_journals([journal], session, incremental=False)
    session.commit()
    assert stats.frames > 0  # full file re-read
    session.close()


def test_odds_history_records_changes(tmp_path: Path) -> None:
    from src.db.models import OddsHistory

    journal = tmp_path / "j.jsonl"
    block_v1 = _football_event_block(with_result=False)
    block_v2 = _football_event_block(with_result=False)
    block_v2["events"][0]["data"]["oddValues"] = ["1.55", "5.00"]  # home moved 1.50 -> 1.55
    records = [
        {"t": 1.0, "kind": "open", "url": "wss://x"},
        _frame_out(1, "/eventBlocks/event/data"),
        _frame_in(1, [block_v1]),
        _frame_out(2, "/eventBlocks/event/data"),
        _frame_in(2, [block_v2]),
        {"t": 99.0, "kind": "close", "url": "wss://x"},
    ]
    with journal.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    session, _ = _make_session(tmp_path)
    ingest_journal(journal, session)
    session.commit()

    history = sorted(
        session.scalars(select(OddsHistory).where(OddsHistory.slot == 0)).all(),
        key=lambda r: r.snapshot_ts,
    )
    assert len(history) == 2
    assert [h.odds for h in history] == [1.50, 1.55]

    history_unchanged = session.scalars(
        select(OddsHistory).where(OddsHistory.slot == 1)
    ).all()
    assert len(history_unchanged) == 1
    assert history_unchanged[0].odds == 5.00
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
