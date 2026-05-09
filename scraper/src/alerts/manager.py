from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from src.alerts.base import AlertChannel, Severity, severity_at_least
from src.alerts.console import ConsoleChannel
from src.alerts.discord import DiscordChannel
from src.alerts.email import EmailChannel
from src.alerts.slack import SlackChannel
from src.alerts.telegram import TelegramChannel
from src.config import AlertChannelConfig, Alerts, Secrets


log = logging.getLogger(__name__)


@dataclass
class _Bound:
    channel: AlertChannel
    severity_min: Severity


class ChannelManager:
    """Owns the configured channels and fans an alert out to whichever ones
    accept its severity. Failures on one channel never block the others."""

    def __init__(self, channels: list[_Bound]) -> None:
        self._channels = channels

    @property
    def channels(self) -> list[AlertChannel]:
        return [b.channel for b in self._channels]

    async def send(self, severity: Severity, title: str, body: str) -> list[tuple[str, BaseException | None]]:
        results: list[tuple[str, BaseException | None]] = []
        coros = []
        names = []
        for bound in self._channels:
            if not severity_at_least(severity, bound.severity_min):
                continue
            coros.append(bound.channel.send(severity, title, body))
            names.append(bound.channel.name)
        if not coros:
            return results
        gathered = await asyncio.gather(*coros, return_exceptions=True)
        for name, outcome in zip(names, gathered):
            err = outcome if isinstance(outcome, BaseException) else None
            if err is not None:
                log.warning("alert channel %s failed: %s", name, err)
            results.append((name, err))
        return results


def build_manager(alerts_cfg: Alerts, secrets: Secrets) -> ChannelManager:
    bound: list[_Bound] = []
    for c in alerts_cfg.channels:
        if not c.enabled:
            continue
        try:
            channel = _build_channel(c, secrets)
        except ValueError as exc:
            log.warning("skipping channel %s: %s", c.kind, exc)
            continue
        bound.append(_Bound(channel=channel, severity_min=c.severity_min))
    return ChannelManager(bound)


def _build_channel(c: AlertChannelConfig, secrets: Secrets) -> AlertChannel:
    if c.kind == "console":
        return ConsoleChannel()
    if c.kind == "telegram":
        return TelegramChannel(
            bot_token=secrets.telegram_bot_token or "",
            chat_id=secrets.telegram_chat_id or "",
        )
    if c.kind == "discord":
        return DiscordChannel(webhook_url=secrets.discord_webhook_url or "")
    if c.kind == "slack":
        return SlackChannel(webhook_url=secrets.slack_webhook_url or "")
    if c.kind == "email":
        recipients = [r.strip() for r in (secrets.email_to or "").split(",") if r.strip()]
        return EmailChannel(
            api_key=secrets.resend_api_key or "",
            sender=secrets.email_from or "",
            recipients=recipients,
        )
    raise ValueError(f"unknown channel kind: {c.kind}")
