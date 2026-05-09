from __future__ import annotations

import html

import httpx

from src.alerts.base import AlertChannel, Severity


class TelegramChannel(AlertChannel):
    """Telegram Bot API channel using HTML parse mode.

    HTML mode is used (instead of Markdown) because alert bodies are full of
    characters that Markdown treats specially - underscores in metric names
    like `frames_per_min`, periods in numbers, brackets, etc. - and Telegram
    400s on any unbalanced delimiter. HTML only requires escaping `<`, `>`,
    `&`, so the body can contain anything else freely.
    """

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
        message = (
            f"<b>[{html.escape(severity.upper())}]</b> {html.escape(title)}\n"
            f"{html.escape(body)}"
        )
        url = f"{self._api_base}/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        client = self._client or httpx.AsyncClient(timeout=10.0)
        try:
            r = await client.post(url, json=payload)
            r.raise_for_status()
        finally:
            if self._client is None:
                await client.aclose()
