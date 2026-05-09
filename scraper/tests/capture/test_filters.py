from __future__ import annotations

from src.capture.filters import should_keep_payload


def test_no_patterns_keeps_everything() -> None:
    assert should_keep_payload('{"x":1}', [])


def test_matching_pattern_drops_payload() -> None:
    assert not should_keep_payload(
        '{"req":{"resource":"/tickets/findByTime"}}',
        ["/tickets/findByTime"],
    )


def test_non_matching_pattern_keeps_payload() -> None:
    assert should_keep_payload(
        '{"req":{"resource":"/playlists/all"}}',
        ["/tickets/findByTime"],
    )


def test_empty_pattern_is_ignored() -> None:
    assert should_keep_payload('{"x":1}', [""])
