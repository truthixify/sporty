"""Walks the DB and writes the export tree to disk.

Layout:

    output_dir/
      manifest.json
      football/
        schemas/
          <schema_id>_<slug>/
            schema.json
            seasons/
              <season_index>/
                season.json
                matchday-<NN>.json                 (leagues)
                <PHASE>/matchday-<NN>.json         (tournaments)
            all-events.ndjson
        all-events.ndjson
      dogs/
        schemas/
          <schema_id>_<slug>/
            schema.json
            races/
              <YYYY-MM-DD>/race-<e_block_id>.json
            all-races.ndjson
        all-races.ndjson
      ...
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Event, MatchDaySnapshot, Schema, Season
from src.export import builders
from src.export.markets import preload_markets


_RACE_PRODUCTS = ("dogs", "horses", "speedway", "motorbikes", "mma")


@dataclass
class ExportStats:
    output_dir: Path
    schemas: int = 0
    seasons: int = 0
    matchdays: int = 0
    races: int = 0
    events: int = 0
    files_written: int = 0
    products_written: list[str] = field(default_factory=list)


def run_export(
    session: Session,
    output_dir: Path,
    *,
    product: str | None = None,
    schema_id: int | None = None,
    season_id: int | None = None,
    since: str | None = None,
    until: str | None = None,
    bundle: bool = False,
) -> ExportStats:
    """Walk the DB and write the full export tree under `output_dir`. Returns
    a stats summary. If `bundle=True`, the tree is zipped to
    `output_dir.with_suffix(".zip")` and the temporary directory is removed.
    """
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    stats = ExportStats(output_dir=output_dir)

    products = _resolve_products(session, product)
    for prod in products:
        if prod == "football":
            _export_football(session, output_dir / "football", stats,
                             schema_id=schema_id, season_id=season_id,
                             since=since, until=until)
        elif prod in _RACE_PRODUCTS:
            _export_race_product(session, output_dir / prod, stats, product=prod,
                                 schema_id=schema_id, since=since, until=until)
        stats.products_written.append(prod)

    manifest = builders.build_manifest(
        session,
        products=stats.products_written,
        schema_count=stats.schemas,
        event_count=stats.events,
        season_count=stats.seasons,
        matchday_count=stats.matchdays,
        race_count=stats.races,
        filters={
            "product": product,
            "schema_id": schema_id,
            "season_id": season_id,
            "since": since,
            "until": until,
        },
    )
    _write_json(output_dir / "manifest.json", manifest)
    stats.files_written += 1

    if bundle:
        zip_path = output_dir.with_suffix(".zip")
        if zip_path.exists():
            zip_path.unlink()
        shutil.make_archive(str(output_dir), "zip", root_dir=output_dir)
        shutil.rmtree(output_dir)
        stats.output_dir = zip_path

    return stats


def _resolve_products(session: Session, product: str | None) -> list[str]:
    if product is not None:
        return [product]
    rows = session.scalars(select(Schema.product).distinct()).all()
    # Stable order: football first, then races in canonical order, then any unknown
    ordered = []
    for p in ("football", *_RACE_PRODUCTS):
        if p in rows:
            ordered.append(p)
    for p in rows:
        if p not in ordered:
            ordered.append(p)
    return ordered


def _export_football(
    session: Session,
    out_dir: Path,
    stats: ExportStats,
    *,
    schema_id: int | None,
    season_id: int | None,
    since: str | None,
    until: str | None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    schema_q = select(Schema).where(Schema.product == "football")
    if schema_id is not None:
        schema_q = schema_q.where(Schema.schema_id == schema_id)
    schemas = session.scalars(schema_q.order_by(Schema.schema_id)).all()
    if not schemas:
        return

    schemas_dir = out_dir / "schemas"
    schemas_dir.mkdir(exist_ok=True)
    all_events_path = out_dir / "all-events.ndjson"
    all_events_f = all_events_path.open("w", encoding="utf-8")
    stats.files_written += 1

    try:
        for s in schemas:
            schema_dir_name = f"{s.schema_id}_{builders.slug(s.description)}"
            schema_dir = schemas_dir / schema_dir_name
            schema_dir.mkdir(exist_ok=True)

            _write_json(schema_dir / "schema.json", builders.build_schema(session, s.schema_id))
            stats.files_written += 1
            stats.schemas += 1

            seasons_q = select(Season).where(Season.schema_id == s.schema_id)
            if season_id is not None:
                seasons_q = seasons_q.where(Season.season_id == season_id)
            seasons = session.scalars(seasons_q.order_by(Season.season_index)).all()

            if seasons:
                seasons_dir = schema_dir / "seasons"
                seasons_dir.mkdir(exist_ok=True)
                for season in seasons:
                    _export_one_season(session, seasons_dir, season, stats)

            # Per-schema rollup + add to global rollup
            schema_rollup = schema_dir / "all-events.ndjson"
            with schema_rollup.open("w", encoding="utf-8") as f:
                stats.files_written += 1
                markets = preload_markets(session, [s.schema_id]).get(s.schema_id, [])
                events_q = (
                    select(Event)
                    .where(Event.schema_id == s.schema_id, Event.product == "football")
                    .order_by(Event.event_time, Event.e_block_id)
                )
                if since is not None:
                    events_q = events_q.where(Event.event_time >= since)
                if until is not None:
                    events_q = events_q.where(Event.event_time <= until)
                for ev in session.scalars(events_q):
                    row = builders.event_row_for_rollup(session, ev, markets)
                    line = json.dumps(row, ensure_ascii=False) + "\n"
                    f.write(line)
                    all_events_f.write(line)
                    stats.events += 1
    finally:
        all_events_f.close()


def _export_one_season(
    session: Session,
    seasons_dir: Path,
    season: Season,
    stats: ExportStats,
) -> None:
    season_dir = seasons_dir / str(season.season_index)
    season_dir.mkdir(exist_ok=True)
    _write_json(season_dir / "season.json", builders.build_season(session, season.season_id))
    stats.files_written += 1
    stats.seasons += 1

    snaps = session.scalars(
        select(MatchDaySnapshot)
        .where(MatchDaySnapshot.season_id == season.season_id)
        .order_by(MatchDaySnapshot.phase, MatchDaySnapshot.match_day)
    ).all()
    for snap in snaps:
        if snap.phase:  # tournament
            phase_dir = season_dir / snap.phase
            phase_dir.mkdir(exist_ok=True)
            target = phase_dir / f"matchday-{snap.match_day:02d}.json"
        else:
            target = season_dir / f"matchday-{snap.match_day:02d}.json"
        _write_json(
            target,
            builders.build_matchday(session, season.season_id, snap.phase, snap.match_day),
        )
        stats.files_written += 1
        stats.matchdays += 1


def _export_race_product(
    session: Session,
    out_dir: Path,
    stats: ExportStats,
    *,
    product: str,
    schema_id: int | None,
    since: str | None,
    until: str | None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    schema_q = select(Schema).where(Schema.product == product)
    if schema_id is not None:
        schema_q = schema_q.where(Schema.schema_id == schema_id)
    schemas = session.scalars(schema_q.order_by(Schema.schema_id)).all()
    if not schemas:
        return

    schemas_dir = out_dir / "schemas"
    schemas_dir.mkdir(exist_ok=True)
    all_races_path = out_dir / "all-races.ndjson"
    all_races_f = all_races_path.open("w", encoding="utf-8")
    stats.files_written += 1

    try:
        for s in schemas:
            schema_dir_name = f"{s.schema_id}_{builders.slug(s.description)}"
            schema_dir = schemas_dir / schema_dir_name
            schema_dir.mkdir(exist_ok=True)

            _write_json(schema_dir / "schema.json", builders.build_schema(session, s.schema_id))
            stats.files_written += 1
            stats.schemas += 1

            races_dir = schema_dir / "races"
            races_dir.mkdir(exist_ok=True)

            events_q = (
                select(Event)
                .where(Event.schema_id == s.schema_id, Event.product == product)
                .order_by(Event.event_time, Event.e_block_id)
            )
            if since is not None:
                events_q = events_q.where(Event.event_time >= since)
            if until is not None:
                events_q = events_q.where(Event.event_time <= until)
            events = session.scalars(events_q).all()

            schema_rollup = schema_dir / "all-races.ndjson"
            with schema_rollup.open("w", encoding="utf-8") as f:
                stats.files_written += 1
                markets = preload_markets(session, [s.schema_id]).get(s.schema_id, [])
                for ev in events:
                    payload = builders.build_race(session, ev.e_block_id)
                    date_dir_name = (ev.event_time or "")[:10] or "unknown"
                    date_dir = races_dir / date_dir_name
                    date_dir.mkdir(exist_ok=True)
                    _write_json(
                        date_dir / f"race-{ev.e_block_id}.json",
                        payload,
                    )
                    stats.files_written += 1
                    stats.races += 1
                    stats.events += 1

                    rollup_row = builders.event_row_for_rollup(session, ev, markets)
                    line = json.dumps(rollup_row, ensure_ascii=False) + "\n"
                    f.write(line)
                    all_races_f.write(line)
    finally:
        all_races_f.close()


def _write_json(path: Path, payload: dict | list) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
