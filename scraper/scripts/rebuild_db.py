"""Drop the scraper database and rebuild it by re-parsing every journal file.
Use after a parser change you want to apply to historical data, or after a
schema migration that needs a clean slate.

Usage: `uv run python scripts/rebuild_db.py [--source DIR]`
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

from src.config import load_config
from src.db import init_db, make_engine, make_session_factory
from src.parse import aggregate_matchdays, detect_seasons, ingest_journals


def _delete_sqlite_file(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme.startswith("sqlite"):
        # sqlite URL is sqlite:///./path/to.db
        db_path = url.split("sqlite:///", 1)[-1]
        path = Path(db_path)
        if path.exists():
            path.unlink()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=None)
    args = ap.parse_args()

    cfg = load_config()
    src = args.source or cfg.paths.captures_dir
    if not src.exists():
        print(f"source not found: {src}", file=sys.stderr)
        return 2

    _delete_sqlite_file(cfg.database.url)
    init_db(config=cfg)

    files = sorted(p for p in src.glob("*.jsonl") if p.is_file())
    if not files:
        print(f"no .jsonl files in {src}", file=sys.stderr)
        return 2

    engine = make_engine(cfg)
    Session = make_session_factory(engine)
    with Session() as session:
        stats = ingest_journals(files, session)
        n_seasons = detect_seasons(
            session, require_standings_reset=cfg.parse.season_detector.require_standings_reset
        )
        n_snaps = aggregate_matchdays(session)
        session.commit()

    print(f"rebuilt: files={stats.files} events={stats.events} seasons_new={n_seasons} matchday_snapshots={n_snaps}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
