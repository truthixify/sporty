from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect

from src.config import Config, Database, Paths
from src.db import init_db


EXPECTED_TABLES = {
    "schemas",
    "schema_markets",
    "schema_participants",
    "seasons",
    "events",
    "odds",
    "odds_history",
    "event_participants_football",
    "event_runners",
    "standings",
    "match_day_snapshots",
    "parse_watermarks",
    "capture_sessions",
    "metrics_snapshots",
    "alerts_log",
    "alembic_version",
}


def _make_config(tmp_path: Path) -> Config:
    return Config(
        paths=Paths(data_dir=tmp_path),
        database=Database(url=f"sqlite:///{tmp_path / 'scraper.db'}"),
    )


def test_init_db_creates_all_tables(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    init_db(config=cfg)

    engine = create_engine(cfg.database.url)
    tables = set(inspect(engine).get_table_names())
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables: {missing}"


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    init_db(config=cfg)
    init_db(config=cfg)

    engine = create_engine(cfg.database.url)
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES.issubset(tables)


def test_init_db_creates_data_dir(tmp_path: Path) -> None:
    nested = tmp_path / "deeper" / "data"
    cfg = Config(
        paths=Paths(data_dir=nested),
        database=Database(url=f"sqlite:///{nested / 'scraper.db'}"),
    )
    init_db(config=cfg)
    assert nested.exists()
    assert (nested / "scraper.db").exists()
