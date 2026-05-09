from __future__ import annotations

import time
from collections import deque
from typing import Deque, Tuple


class HealthMonitor:
    """Track recent inbound RESPONSE statuses to decide whether the WS session
    has died (too many 401s in the rolling window) or has recovered (steady
    200s for the post-recovery probation window)."""

    def __init__(
        self,
        *,
        window_seconds: float = 60.0,
        death_401_threshold: int = 5,
        healthy_after_seconds: float = 30.0,
        healthy_min_200s: int = 5,
    ) -> None:
        self.window_seconds = window_seconds
        self.death_401_threshold = death_401_threshold
        self.healthy_after_seconds = healthy_after_seconds
        self.healthy_min_200s = healthy_min_200s
        self._statuses: Deque[Tuple[float, int]] = deque()
        self._reset_ts: float | None = None

    def record(self, ts: float, status: int) -> None:
        self._statuses.append((ts, status))
        cutoff = ts - max(self.window_seconds, self.healthy_after_seconds)
        while self._statuses and self._statuses[0][0] < cutoff:
            self._statuses.popleft()

    def reset(self, now: float | None = None) -> None:
        self._statuses.clear()
        self._reset_ts = time.time() if now is None else now

    def is_dead(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        cutoff = now - self.window_seconds
        n401 = sum(1 for ts, st in self._statuses if ts >= cutoff and st == 401)
        return n401 >= self.death_401_threshold

    def is_healthy_after_recovery(self, now: float | None = None) -> bool:
        if self._reset_ts is None:
            return False
        now = time.time() if now is None else now
        if now - self._reset_ts < self.healthy_after_seconds:
            return False
        cutoff = now - self.healthy_after_seconds
        recent = [(ts, st) for ts, st in self._statuses if ts >= cutoff]
        if not recent:
            return False
        n401 = sum(1 for _, st in recent if st == 401)
        n200 = sum(1 for _, st in recent if st == 200)
        return n401 == 0 and n200 >= self.healthy_min_200s
