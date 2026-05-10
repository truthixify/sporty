from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    Event,
    EventParticipantFootball,
    EventRunner,
    Odds,
    OddsHistory,
    ParseWatermark,
    Schema,
    SchemaMarket,
    SchemaParticipant,
    Standing,
)
from src.parse.journal import iter_frames_with_offsets
from src.parse.pairing import Pairer, ResponsePair
from src.parse.products import (
    all_products,
    detect_for_event_block,
    detect_for_schema_template,
)
from src.parse.schemas import schema_rows_from_template


_PRODUCTS_BY_NAME = {p.name: p for p in all_products()}


@dataclass
class IngestStats:
    files: int = 0
    frames: int = 0
    pairs: int = 0
    schemas: int = 0
    events: int = 0
    odds: int = 0
    standings: int = 0
    runners: int = 0
    participants: int = 0
    unknown_blocks: int = 0
    skipped_resources: dict[str, int] = field(default_factory=dict)


class _Buffer:
    """In-memory dedup buffer keyed by primary key. Combined with a single
    `flush` at the end, this avoids the within-transaction UNIQUE conflicts you
    get from naively re-merging the same PK across many journal frames."""

    def __init__(self) -> None:
        self.schemas: dict[int, Schema] = {}
        self.markets: dict[tuple[int, int], SchemaMarket] = {}
        self.participants: dict[tuple[int, str], SchemaParticipant] = {}
        self.events: dict[int, Event] = {}
        self.odds: dict[tuple[int, int], Odds] = {}
        self.odds_history: dict[tuple[int, int, float], OddsHistory] = {}
        self._last_odds_value: dict[tuple[int, int], float] = {}
        self.standings: dict[tuple[int, str], Standing] = {}
        self.fb_parts: dict[tuple[int, str], EventParticipantFootball] = {}
        self.runners: dict[tuple[int, str], EventRunner] = {}

    def upsert_schema(self, schema: Schema) -> None:
        existing = self.schemas.get(schema.schema_id)
        if existing is None:
            self.schemas[schema.schema_id] = schema
            return
        existing.last_seen_ts = max(existing.last_seen_ts, schema.last_seen_ts)
        for col in Schema.__table__.columns:
            new_val = schema.__dict__.get(col.name)
            if col.name in ("first_seen_ts", "last_seen_ts"):
                continue
            if new_val is not None:
                setattr(existing, col.name, new_val)

    def upsert_event(self, event: Event) -> None:
        existing = self.events.get(event.e_block_id)
        if existing is None:
            self.events[event.e_block_id] = event
            return
        for col in Event.__table__.columns:
            new_val = event.__dict__.get(col.name)
            if new_val is not None:
                setattr(existing, col.name, new_val)

    def upsert_market(self, m: SchemaMarket) -> None:
        self.markets[(m.schema_id, m.slot)] = m

    def upsert_participant(self, p: SchemaParticipant) -> None:
        self.participants[(p.schema_id, p.team_id)] = p

    def upsert_odds(self, o: Odds, snapshot_ts: float) -> None:
        key = (o.e_block_id, o.slot)
        prev = self._last_odds_value.get(key)
        # Only record a history row when the price actually moved within this
        # ingest run. Cross-run movement is captured the next time the value
        # changes; the spec accepts that approximation.
        if prev is None or prev != o.odds:
            self.odds_history[(o.e_block_id, o.slot, snapshot_ts)] = OddsHistory(
                e_block_id=o.e_block_id,
                slot=o.slot,
                snapshot_ts=snapshot_ts,
                odds=o.odds,
            )
            self._last_odds_value[key] = o.odds
        self.odds[key] = o

    def upsert_standing(self, s: Standing) -> None:
        self.standings[(s.e_block_id, s.team_id)] = s

    def upsert_fb_part(self, fp: EventParticipantFootball) -> None:
        self.fb_parts[(fp.e_block_id, fp.side)] = fp

    def upsert_runner(self, r: EventRunner) -> None:
        self.runners[(r.e_block_id, r.runner_id)] = r

    def flush(self, session: Session) -> None:
        for s in self.schemas.values():
            existing = session.get(Schema, s.schema_id)
            if existing is not None:
                s.first_seen_ts = min(existing.first_seen_ts, s.first_seen_ts)
            session.merge(s)
        for m in self.markets.values():
            session.merge(m)
        for p in self.participants.values():
            session.merge(p)
        for e in self.events.values():
            session.merge(e)
        for o in self.odds.values():
            session.merge(o)
        for h in self.odds_history.values():
            session.merge(h)
        for fp in self.fb_parts.values():
            session.merge(fp)
        for r in self.runners.values():
            session.merge(r)
        for st in self.standings.values():
            session.merge(st)


@dataclass
class _FileProgress:
    final_offset: int
    final_line: int
    parsed_lines: int


def ingest_journal(
    path: Path,
    session: Session,
    stats: IngestStats | None = None,
    buffer: _Buffer | None = None,
    *,
    start_offset: int = 0,
    start_line: int = 0,
) -> tuple[IngestStats, _FileProgress]:
    """Read one journal file from `start_offset` and stage parsed rows for the
    session. Returns updated stats and a `_FileProgress` carrying the byte
    offset / line number that should be persisted as the next watermark.

    A single buffer can be reused across multiple files (see `ingest_journals`)
    to dedupe rows that recur in different files. If no buffer is passed in,
    one is created and flushed at the end of this call.
    """
    stats = stats or IngestStats()
    own_buffer = buffer is None
    buf = buffer if buffer is not None else _Buffer()
    pairer = Pairer()
    stats.files += 1
    final_offset = start_offset
    final_line = start_line
    consumed = 0

    for frame, offset, line_no in iter_frames_with_offsets(
        path, start_offset=start_offset, start_line=start_line,
    ):
        stats.frames += 1
        consumed += 1
        final_offset = offset
        final_line = line_no
        if frame.kind in ("open", "close"):
            pairer.reset()
            continue
        if frame.kind == "iframe_url":
            continue
        if frame.payload is None:
            continue
        if frame.direction == "out":
            pairer.feed_request(frame.ts, frame.payload)
            continue
        if frame.direction == "in":
            pair = pairer.feed_response(frame.ts, frame.payload)
            if pair is None:
                continue
            stats.pairs += 1
            if pair.status_code != 200:
                continue
            _route(pair, buf, stats)

    if own_buffer:
        buf.flush(session)
        session.flush()

    return stats, _FileProgress(final_offset=final_offset, final_line=final_line, parsed_lines=consumed)


def ingest_journals(
    paths: Iterable[Path],
    session: Session,
    *,
    incremental: bool = False,
) -> IngestStats:
    """Backfill or incremental parse over a list of journal files.

    With `incremental=True`, each file is resumed from the byte offset stored
    in `parse_watermarks`. With the default `incremental=False`, every file is
    re-read from offset 0 (the parse is idempotent at the row level via
    `session.merge`, but the work is wasted if the file hasn't changed).

    After all files are flushed and the session is committed by the caller,
    `parse_watermarks` rows are written so the next incremental run picks up
    from where this one left off.
    """
    stats = IngestStats()
    buffer = _Buffer()
    progress: dict[str, _FileProgress] = {}

    watermarks: dict[str, tuple[int, int]] = {}
    if incremental:
        for w in session.scalars(select(ParseWatermark)):
            watermarks[w.journal_file] = (w.last_offset, w.last_line)

    paths = [Path(p) for p in paths]
    for path in paths:
        # Always use the resolved absolute path as the watermark key so
        # different cwds produce the same key and don't end up with
        # duplicate rows for the same physical file.
        key = _watermark_key(path)
        start_offset, start_line = watermarks.get(key, (0, 0))
        _, prog = ingest_journal(
            path, session, stats, buffer,
            start_offset=start_offset, start_line=start_line,
        )
        progress[key] = prog

    buffer.flush(session)
    session.flush()
    _update_watermarks(session, progress)
    return stats


def _watermark_key(path: Path) -> str:
    """Canonical key for `parse_watermarks.journal_file`. We resolve to an
    absolute path so the same file isn't tracked twice when run from
    different cwds (`./j.jsonl` vs `/full/path/j.jsonl`)."""
    try:
        return str(path.resolve())
    except (OSError, RuntimeError):
        return str(path)


def _update_watermarks(session: Session, progress: dict[str, _FileProgress]) -> None:
    """Persist the latest position for each journal file. `parsed_count` is
    the count from the most recent run, not a lifetime sum, so a backfill
    overwrites rather than inflating the value."""
    now = time.time()
    for key, prog in progress.items():
        existing = session.get(ParseWatermark, key)
        if existing is not None:
            existing.last_offset = prog.final_offset
            existing.last_line = prog.final_line
            existing.parsed_count = prog.parsed_lines
            existing.last_run_ts = now
        else:
            session.add(ParseWatermark(
                journal_file=key,
                last_offset=prog.final_offset,
                last_line=prog.final_line,
                parsed_count=prog.parsed_lines,
                last_run_ts=now,
            ))
    session.flush()


def _route(pair: ResponsePair, buf: _Buffer, stats: IngestStats) -> None:
    res = pair.resource
    if "/playlists/" in res:
        _stage_schemas(pair, buf, stats)
    elif res in ("/eventBlocks/event/data", "/eventBlocks/event/result"):
        _stage_event_blocks(pair, buf, stats)
    elif res == "/eventBlocks/stats":
        _stage_stats_blocks(pair, buf, stats)
    else:
        stats.skipped_resources[res] = stats.skipped_resources.get(res, 0) + 1


def _stage_schemas(pair: ResponsePair, buf: _Buffer, stats: IngestStats) -> None:
    body = pair.body
    if not isinstance(body, list):
        return
    for tpl in body:
        if not isinstance(tpl, dict):
            continue
        product = detect_for_schema_template(tpl)
        if product is None:
            continue
        schema, markets, participants = schema_rows_from_template(
            tpl, product.name, pair.response_ts,
        )
        if schema.schema_id is None:
            continue
        buf.upsert_schema(schema)
        for m in markets:
            buf.upsert_market(m)
        for p in participants:
            buf.upsert_participant(p)
        stats.schemas += 1


def _stage_event_blocks(pair: ResponsePair, buf: _Buffer, stats: IngestStats) -> None:
    body = pair.body
    if not isinstance(body, list):
        return
    for block in body:
        if not isinstance(block, dict):
            continue
        product = detect_for_event_block(block)
        if product is None:
            # Result blocks (and metadata-only blocks) often have no
            # `participants[*].classType` to dispatch on. Fall back to the
            # product we already classified for this `playlistId` from an
            # earlier /event/data or /playlists/ frame.
            schema_id = block.get("playlistId")
            if isinstance(schema_id, int) and schema_id in buf.schemas:
                product = _PRODUCTS_BY_NAME.get(buf.schemas[schema_id].product)
        if product is None:
            stats.unknown_blocks += 1
            continue
        events, odds, fb_parts, runners, _ = product.extract_event_rows(
            block, pair.resource, pair.response_ts,
        )
        for e in events:
            buf.upsert_event(e)
            _ensure_schema_placeholder(buf, e.schema_id, e.product, pair.response_ts)
            stats.events += 1
        for o in odds:
            buf.upsert_odds(o, snapshot_ts=pair.response_ts)
            stats.odds += 1
        for fp in fb_parts:
            buf.upsert_fb_part(fp)
            stats.participants += 1
        for r in runners:
            buf.upsert_runner(r)
            stats.runners += 1


def _ensure_schema_placeholder(buf: _Buffer, schema_id: int | None, product: str, ts: float) -> None:
    """Race products receive their `/playlists/` only if the user navigates to
    that sport in the iframe; the prototype's captures rarely include them.
    Without a Schema row for the event's schema_id, we'd have FK-orphaned rows
    and no way to label the league. So when an event references a schema we
    haven't seen, drop in a minimal placeholder. A real schema row arriving
    later overwrites the placeholder fields via Buffer.upsert_schema.
    """
    if schema_id is None or schema_id in buf.schemas:
        return
    buf.upsert_schema(Schema(
        schema_id=schema_id,
        product=product,
        kind="unknown",
        first_seen_ts=ts,
        last_seen_ts=ts,
    ))


def _stage_stats_blocks(pair: ResponsePair, buf: _Buffer, stats: IngestStats) -> None:
    body = pair.body
    if not isinstance(body, list):
        return
    for block in body:
        if not isinstance(block, dict):
            continue
        product = detect_for_event_block(block)
        if product is None:
            continue
        for s in product.extract_stats_rows(block, pair.response_ts):
            buf.upsert_standing(s)
            stats.standings += 1
