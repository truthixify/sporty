from __future__ import annotations

import httpx

from src.alerts.base import AlertChannel, Severity


class TelegramChannel(AlertChannel):
    """Telegram Bot API channel. Uses the synchronous HTTP API via httpx."""

    name = "telegram"

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        client: httpx.AsyncClient | None = None,
        api_base: str = "https://api.telegram.org",
    ) -> None:
        if not bot_token or not chat_id:
            raise ValueError("telegram channel requires bot_token and chat_id")
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._api_base = api_base.rstrip("/")
        self._client = client

    async def send(self, severity: Severity, title: str, body: str) -> None:
        message = f"*[{severity.upper()}]* {title}\n{body}"
        url = f"{self._api_base}/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        client = self._client or httpx.AsyncClient(timeout=10.0)
        try:
            r = await client.post(url, json=payload)
            r.raise_for_status()
        finally:
            if self._client is None:
                await client.aclose()
