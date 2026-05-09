from __future__ import annotations

from collections import deque
from typing import Deque


class RecoveryRateLimiter:
    """Bound how many recovery attempts may run in a sliding hour window."""

    def __init__(self, *, max_per_hour: int = 6, window_seconds: float = 3600.0) -> None:
        self.max_per_hour = max_per_hour
        self.window_seconds = window_seconds
        self._attempts: Deque[float] = deque()

    def record_attempt(self, ts: float) -> None:
        self._attempts.append(ts)
        cutoff = ts - self.window_seconds
        while self._attempts and self._attempts[0] < cutoff:
            self._attempts.popleft()

    def too_many(self, now: float) -> bool:
        cutoff = now - self.window_seconds
        return sum(1 for t in self._attempts if t >= cutoff) >= self.max_per_hour

    @property
    def attempt_count(self) -> int:
        return len(self._attempts)
