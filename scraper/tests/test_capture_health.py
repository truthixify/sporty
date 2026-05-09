from __future__ import annotations

from src.capture.health import HealthMonitor


def test_not_dead_under_threshold() -> None:
    h = HealthMonitor(window_seconds=60.0, death_401_threshold=5)
    for ts in range(10, 14):
        h.record(float(ts), 401)
    assert not h.is_dead(now=20.0)


def test_dead_after_threshold_within_window() -> None:
    h = HealthMonitor(window_seconds=60.0, death_401_threshold=5)
    for ts in range(10, 15):
        h.record(float(ts), 401)
    assert h.is_dead(now=20.0)


def test_old_401s_drop_out_of_window() -> None:
    h = HealthMonitor(window_seconds=60.0, death_401_threshold=5)
    for ts in range(0, 5):
        h.record(float(ts), 401)
    assert not h.is_dead(now=200.0)


def test_healthy_after_recovery_requires_clean_200s() -> None:
    h = HealthMonitor(
        window_seconds=60.0,
        death_401_threshold=5,
        healthy_after_seconds=10.0,
        healthy_min_200s=3,
    )
    h.reset(now=100.0)
    assert not h.is_healthy_after_recovery(now=105.0)
    for ts in [111.0, 112.0, 113.0, 114.0]:
        h.record(ts, 200)
    assert h.is_healthy_after_recovery(now=115.0)


def test_healthy_check_fails_if_any_401_present() -> None:
    h = HealthMonitor(
        window_seconds=60.0, death_401_threshold=5,
        healthy_after_seconds=10.0, healthy_min_200s=3,
    )
    h.reset(now=100.0)
    for ts in [111.0, 112.0, 113.0]:
        h.record(ts, 200)
    h.record(114.0, 401)
    assert not h.is_healthy_after_recovery(now=115.0)
