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
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            frame = _normalize(rec)
            if frame is not None:
                yield frame


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
