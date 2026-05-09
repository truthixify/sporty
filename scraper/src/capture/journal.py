from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import IO, Any, Literal


CAPTURE_VERSION = 1
LifecycleKind = Literal["open", "close", "iframe_url"]


def new_session_id() -> str:
    return uuid.uuid4().hex


class JournalWriter:
    """Append-only JSONL writer with daily rotation. Each record carries the
    daemon's session_id and the capture schema version, so a downstream parser
    can tell frames from different daemon runs apart."""

    def __init__(
        self,
        captures_dir: Path,
        session_id: str,
        *,
        capture_version: int = CAPTURE_VERSION,
        clock=time.time,
    ) -> None:
        self.captures_dir = captures_dir
        self.session_id = session_id
        self.capture_version = capture_version
        self._clock = clock
        self._date_key: str = ""
        self._file: IO[str] | None = None
        self.captures_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, date_key: str) -> Path:
        return self.captures_dir / f"vs_{date_key}.jsonl"

    def _rotate_if_needed(self, ts: float) -> None:
        date_key = time.strftime("%Y-%m-%d", time.gmtime(ts))
        if self._file is not None and date_key == self._date_key:
            return
        if self._file is not None:
            self._file.close()
        self._date_key = date_key
        self._file = self.path_for(date_key).open("a", encoding="utf-8", buffering=1)

    def _write(self, record: dict[str, Any]) -> None:
        ts = self._clock()
        self._rotate_if_needed(ts)
        record["ts"] = ts
        record["session_id"] = self.session_id
        record["capture_version"] = self.capture_version
        assert self._file is not None
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._file.flush()

    def lifecycle(self, kind: LifecycleKind, url: str | None = None) -> None:
        self._write({"kind": kind, "url": url})

    def frame(self, direction: Literal["in", "out"], data: str) -> None:
        self._write({"kind": "frame", "dir": direction, "data": data})

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
