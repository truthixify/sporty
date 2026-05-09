from __future__ import annotations

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
