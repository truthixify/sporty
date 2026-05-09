from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal


FrameKind = Literal["open", "close", "iframe_url", "frame"]
Direction = Literal["in", "out"]


@dataclass(slots=True, frozen=True)
class JournalFrame:
    """One normalized line from a capture journal.

    The legacy prototype journal uses keys `t`, `dir`, `kind`, `data`, `url`.
    The new daemon will write `ts`, `dir`, `kind`, `data`, `session_id`,
    `capture_version`. This frame type accepts both shapes; downstream code
    works with the normalized fields.
    """

    ts: float
    kind: FrameKind
    direction: Direction | None
    payload: dict[str, Any] | None
    url: str | None
    session_id: str | None = None


def iter_frames(path: Path) -> Iterator[JournalFrame]:
    """Yield normalized frames from a JSONL journal file. Skips malformed lines."""
    for frame, _, _ in iter_frames_with_offsets(path):
        yield frame


def iter_frames_with_offsets(
    path: Path,
    *,
    start_offset: int = 0,
    start_line: int = 0,
) -> Iterator[tuple[JournalFrame, int, int]]:
    """Yield `(frame, end_offset, line_no)` tuples, where `end_offset` is the
    file's tell() right after the line was read (so it can be persisted as a
    watermark) and `line_no` is the absolute 1-based line number assuming the
    caller passes `start_line` as the line count already consumed.

    Reads in binary mode so byte offsets are stable regardless of any newline
    translation. Malformed JSON lines are silently skipped, but their offset
    still advances so a re-run won't hit them again.
    """
    line_no = start_line
    with path.open("rb") as f:
        if start_offset > 0:
            f.seek(start_offset)
        while True:
            raw = f.readline()
            if not raw:
                break
            line_no += 1
            offset = f.tell()
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            frame = _normalize(rec)
            if frame is not None:
                yield frame, offset, line_no


def _normalize(rec: dict[str, Any]) -> JournalFrame | None:
    ts = rec.get("t") if "t" in rec else rec.get("ts")
    if not isinstance(ts, (int, float)):
        return None
    ts = float(ts)
    session_id = rec.get("session_id")
    kind_raw = rec.get("kind")
    direction = rec.get("dir")

    if kind_raw in ("open", "close", "iframe_url"):
        return JournalFrame(
            ts=ts, kind=kind_raw, direction=None,
            payload=None, url=rec.get("url"), session_id=session_id,
        )

    if direction in ("in", "out"):
        data = rec.get("data")
        payload: dict[str, Any] | None
        if isinstance(data, str):
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                return None
        elif isinstance(data, dict):
            payload = data
        else:
            return None
        if not isinstance(payload, dict):
            return None
        return JournalFrame(
            ts=ts, kind="frame", direction=direction,
            payload=payload, url=None, session_id=session_id,
        )

    return None
