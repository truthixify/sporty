from __future__ import annotations

import httpx

from src.alerts.base import AlertChannel, Severity


class EmailChannel(AlertChannel):
    """Email channel powered by the Resend HTTP API.

    Uses the `/emails` endpoint with a Bearer-token API key. We deliberately do
    not use SMTP, so there is no STARTTLS handshake or smtplib involvement.
    """

    name = "email"
    DEFAULT_API_BASE = "https://api.resend.com"

    def __init__(
        self,
        *,
        api_key: str,
        sender: str,
        recipients: list[str],
        api_base: str = DEFAULT_API_BASE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not sender or not recipients:
            raise ValueError("email channel requires api_key, sender, and recipients")
        self._api_key = api_key
        self._sender = sender
        self._recipients = recipients
        self._api_base = api_base.rstrip("/")
        self._client = client

    async def send(self, severity: Severity, title: str, body: str) -> None:
        payload = {
            "from": self._sender,
            "to": self._recipients,
            "subject": f"[{severity.upper()}] {title}",
            "text": body,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        client = self._client or httpx.AsyncClient(timeout=10.0)
        try:
            r = await client.post(f"{self._api_base}/emails", headers=headers, json=payload)
            r.raise_for_status()
        finally:
            if self._client is None:
                await client.aclose()
