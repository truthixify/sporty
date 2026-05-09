"""End-to-end ingest against a curated slice of a real prototype journal.

The fixture at `tests/fixtures/sample_capture.jsonl` was cut from a real
SportyBet capture and trimmed to a single representative pair of each
resource (one /playlists/, one /event/data + /event/result for football, one
/event/data + /event/result for dogs, one /eventBlocks/stats). Use it to
catch regressions in the wire-format -> rows pipeline that synthetic test
data can miss."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from src.config import Config, Database, Paths
from src.db import init_db, make_engine, make_session_factory
from src.db.models import Event, Schema
from src.parse import ingest_journal


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_capture.jsonl"


@pytest.mark.skipif(not FIXTURE.exists(), reason="sample_capture.jsonl missing")
def test_real_fixture_round_trip(tmp_path: Path) -> None:
    cfg = Config(
        paths=Paths(data_dir=tmp_path),
        database=Database(url=f"sqlite:///{tmp_path / 'fix.db'}"),
    )
    init_db(config=cfg)
    engine = make_engine(cfg)
    Session = make_session_factory(engine)
    with Session() as session:
        stats, _ = ingest_journal(FIXTURE, session)
        session.commit()

        assert stats.frames > 0
        assert stats.pairs > 0
        assert stats.unknown_blocks == 0

        schemas = session.scalars(select(Schema)).all()
        products = {s.product for s in schemas}
        assert "football" in products

        events = session.scalars(select(Event)).all()
        event_products = {e.product for e in events}
        assert event_products & {"football", "dogs"}
