from __future__ import annotations

import json
import time
from pathlib import Path

from src.capture.journal import JournalWriter, new_session_id


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_writes_lifecycle_and_frame_with_session_id(tmp_path: Path) -> None:
    sid = new_session_id()
    w = JournalWriter(captures_dir=tmp_path, session_id=sid, clock=lambda: 1000.0)
    w.lifecycle("open", url="wss://x")
    w.frame("out", '{"type":"REQUEST","xs":1}')
    w.frame("in", '{"type":"RESPONSE","xs":1,"res":{"statusCode":200}}')
    w.lifecycle("close", url="wss://x")
    w.close()

    expected_path = tmp_path / f"vs_{time.strftime('%Y-%m-%d', time.gmtime(1000.0))}.jsonl"
    assert expected_path.exists()
    rows = _read(expected_path)
    assert len(rows) == 4
    assert all(r["session_id"] == sid for r in rows)
    assert all(r["capture_version"] == 1 for r in rows)
    assert rows[0]["kind"] == "open"
    assert rows[1]["kind"] == "frame" and rows[1]["dir"] == "out"
    assert rows[2]["kind"] == "frame" and rows[2]["dir"] == "in"
    assert rows[3]["kind"] == "close"


def test_rotates_to_new_file_on_date_change(tmp_path: Path) -> None:
    sid = new_session_id()
    times = iter([1.0, 1.5, 86400.0 + 2.0])
    w = JournalWriter(captures_dir=tmp_path, session_id=sid, clock=lambda: next(times))
    w.lifecycle("open", url="a")
    w.frame("out", "{}")
    w.lifecycle("close", url="a")
    w.close()
    assert len(sorted(tmp_path.glob("*.jsonl"))) == 2


def test_session_id_is_unique() -> None:
    a = new_session_id()
    b = new_session_id()
    assert a != b and len(a) == 32
