from __future__ import annotations

import sys
from typing import TextIO

from src.alerts.base import AlertChannel, Severity


class ConsoleChannel(AlertChannel):
    """Print alerts to stderr. Default-on for dev so you always see them."""

    name = "console"

    def __init__(self, *, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stderr

    async def send(self, severity: Severity, title: str, body: str) -> None:
        self._stream.write(f"[alert/{severity}] {title}\n{body}\n")
        self._stream.flush()
