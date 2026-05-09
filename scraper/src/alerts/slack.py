from __future__ import annotations

import httpx

from src.alerts.base import AlertChannel, Severity


class SlackChannel(AlertChannel):
    """Slack incoming-webhook channel."""

    name = "slack"

    def __init__(
        self,
        *,
        webhook_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not webhook_url:
            raise ValueError("slack channel requires webhook_url")
        self._url = webhook_url
        self._client = client

    async def send(self, severity: Severity, title: str, body: str) -> None:
        payload = {"text": f"*[{severity.upper()}]* {title}\n{body}"}
        client = self._client or httpx.AsyncClient(timeout=10.0)
        try:
            r = await client.post(self._url, json=payload)
            r.raise_for_status()
        finally:
            if self._client is None:
                await client.aclose()
