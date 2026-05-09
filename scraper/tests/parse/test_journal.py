from __future__ import annotations

import json
from pathlib import Path

from src.parse.journal import iter_frames


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_iter_frames_legacy_format(tmp_path: Path) -> None:
    p = tmp_path / "journal.jsonl"
    _write_jsonl(p, [
        {"t": 1.0, "kind": "open", "url": "wss://x"},
        {"t": 1.5, "kind": "iframe_url", "url": "https://x"},
        {"t": 2.0, "dir": "out", "data": json.dumps({"type": "REQUEST", "xs": 1, "req": {"resource": "/r"}})},
        {"t": 3.0, "dir": "in", "data": json.dumps({"type": "RESPONSE", "xs": 1, "res": {"statusCode": 200, "body": []}})},
        {"t": 4.0, "kind": "close", "url": "wss://x"},
    ])
    frames = list(iter_frames(p))
    kinds = [(f.kind, f.direction) for f in frames]
    assert kinds == [
        ("open", None),
        ("iframe_url", None),
        ("frame", "out"),
        ("frame", "in"),
        ("close", None),
    ]
    assert frames[2].payload["xs"] == 1
    assert frames[3].payload["res"]["statusCode"] == 200


def test_iter_frames_skips_malformed_lines(tmp_path: Path) -> None:
    p = tmp_path / "journal.jsonl"
    p.write_text(
        '{"t": 1.0, "kind": "open"}\n'
        'not-json\n'
        '{"t": 2.0, "dir": "out", "data": "not-json-payload"}\n'
        '{"t": 3.0, "dir": "in", "data": "{\\"type\\":\\"RESPONSE\\",\\"xs\\":1,\\"res\\":{\\"statusCode\\":200}}"}\n'
        '\n'
    )
    frames = list(iter_frames(p))
    assert len(frames) == 2
    assert frames[0].kind == "open"
    assert frames[1].kind == "frame"


def test_iter_frames_accepts_new_format_with_session_id(tmp_path: Path) -> None:
    p = tmp_path / "journal.jsonl"
    _write_jsonl(p, [
        {"ts": 1.0, "kind": "open", "url": "wss://x", "session_id": "abc"},
        {"ts": 2.0, "dir": "in", "data": {"type": "RESPONSE", "xs": 1, "res": {"statusCode": 200}}, "session_id": "abc"},
    ])
    frames = list(iter_frames(p))
    assert len(frames) == 2
    assert frames[0].session_id == "abc"
    assert frames[1].payload["res"]["statusCode"] == 200
