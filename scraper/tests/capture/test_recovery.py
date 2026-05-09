from __future__ import annotations

import os
from pathlib import Path

from src.capture.browser import cleanup_stale_chromium_lock
from src.capture.recovery import RecoveryRateLimiter


def test_under_budget_allows_more() -> None:
    r = RecoveryRateLimiter(max_per_hour=6)
    for ts in range(5):
        r.record_attempt(float(ts))
    assert not r.too_many(now=100.0)


def test_at_budget_blocks_more() -> None:
    r = RecoveryRateLimiter(max_per_hour=6)
    for ts in range(6):
        r.record_attempt(float(ts))
    assert r.too_many(now=100.0)


def test_old_attempts_age_out() -> None:
    r = RecoveryRateLimiter(max_per_hour=6)
    for ts in range(6):
        r.record_attempt(float(ts))
    assert not r.too_many(now=4000.0)


def test_attempt_count_reflects_pruned_buffer() -> None:
    r = RecoveryRateLimiter(max_per_hour=6)
    for ts in range(6):
        r.record_attempt(float(ts))
    # The seventh attempt at far-future ts prunes the rest.
    r.record_attempt(5000.0)
    assert r.attempt_count == 1


def test_stale_lock_with_dead_pid_is_removed(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    # PID 999999 is overwhelmingly unlikely to be alive
    (profile / "SingletonLock").symlink_to("hostname-999999")
    (profile / "SingletonCookie").write_text("dummy")
    assert cleanup_stale_chromium_lock(profile) is True
    assert not (profile / "SingletonLock").exists()
    assert not (profile / "SingletonCookie").exists()


def test_lock_with_live_non_chromium_pid_is_removed(tmp_path: Path) -> None:
    """When the lock points at a live PID that isn't a chromium-named
    process (e.g. macOS recycled the PID for our shell), we still clean up."""
    profile = tmp_path / "profile"
    profile.mkdir()
    # Our own python process holds os.getpid() — alive but not Chromium
    (profile / "SingletonLock").symlink_to(f"hostname-{os.getpid()}")
    assert cleanup_stale_chromium_lock(profile) is True
    assert not (profile / "SingletonLock").exists()


def test_no_lock_present_is_a_noop(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    assert cleanup_stale_chromium_lock(profile) is False


def test_unparseable_lock_target_is_cleaned_up(tmp_path: Path) -> None:
    """A symlink we can't parse is treated as stale; real chromium always
    writes a `<host>-<pid>` target."""
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "SingletonLock").symlink_to("not-a-pid-format")
    assert cleanup_stale_chromium_lock(profile) is True
    assert not (profile / "SingletonLock").exists()
