from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal


Severity = Literal["info", "warning", "critical"]
SEVERITY_ORDER: dict[Severity, int] = {"info": 0, "warning": 1, "critical": 2}


def severity_at_least(actual: Severity, minimum: Severity) -> bool:
    return SEVERITY_ORDER[actual] >= SEVERITY_ORDER[minimum]


class AlertChannel(ABC):
    """Single notification target (Telegram chat, Discord webhook, console, ...)."""

    name: str

    @abstractmethod
    async def send(self, severity: Severity, title: str, body: str) -> None:
        """Deliver one notification. Implementations may raise on transport
        errors; the manager catches and logs them."""
