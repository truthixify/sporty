from __future__ import annotations

import io

import httpx
import pytest

from src.alerts import (
    ChannelManager,
    ConsoleChannel,
    DiscordChannel,
    EmailChannel,
    SlackChannel,
    TelegramChannel,
    build_manager,
)
from src.alerts.base import severity_at_least
from src.alerts.manager import _Bound
from src.config import AlertChannelConfig, Alerts, Secrets


@pytest.mark.asyncio
async def test_console_channel_writes_to_stream() -> None:
    buf = io.StringIO()
    ch = ConsoleChannel(stream=buf)
    await ch.send("warning", "test", "body line")
    assert "[alert/warning]" in buf.getvalue()
    assert "body line" in buf.getvalue()


@pytest.mark.asyncio
async def test_telegram_uses_bot_api(monkeypatch) -> None:
    captured = {}

    def transport_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = request.read().decode()
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(transport_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ch = TelegramChannel(bot_token="abc", chat_id="42", client=client)
        await ch.send("critical", "down", "things broke")

    assert "/botabc/sendMessage" in captured["url"]
    assert "things broke" in captured["json"]
    import json as _json
    body = _json.loads(captured["json"])
    assert body["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_telegram_escapes_html_in_body() -> None:
    """The alert body is full of underscores and special chars that broke
    Markdown parse mode. HTML mode only requires escaping <, >, &."""
    captured = {}

    def transport_handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.read().decode()
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport_handler)) as client:
        ch = TelegramChannel(bot_token="abc", chat_id="42", client=client)
        await ch.send(
            "warning",
            "capture: low frame rate",
            "frames_per_min=4.2 below 10.0 for 300s. <script>alert(1)</script>",
        )

    payload = captured["json"]
    # Underscores are NOT escaped (they're fine in HTML mode)
    assert "frames_per_min" in payload
    # < and > ARE escaped so the body can't inject HTML
    assert "&lt;script&gt;" in payload
    assert "<script>alert" not in payload


@pytest.mark.asyncio
async def test_telegram_raises_on_missing_secrets() -> None:
    with pytest.raises(ValueError):
        TelegramChannel(bot_token="", chat_id="42")


@pytest.mark.asyncio
async def test_manager_filters_by_severity() -> None:
    sent = []

    class Recorder(ConsoleChannel):
        name = "rec"

        async def send(self, severity, title, body):
            sent.append((severity, title))

    bound = [
        _Bound(channel=Recorder(stream=io.StringIO()), severity_min="critical"),
        _Bound(channel=Recorder(stream=io.StringIO()), severity_min="info"),
    ]
    mgr = ChannelManager(bound)
    await mgr.send("warning", "t", "b")
    assert len(sent) == 1


def test_severity_ordering() -> None:
    assert severity_at_least("critical", "warning")
    assert severity_at_least("warning", "warning")
    assert not severity_at_least("info", "warning")


def test_build_manager_skips_disabled() -> None:
    cfg = Alerts(channels=[
        AlertChannelConfig(kind="telegram", enabled=False),
        AlertChannelConfig(kind="console", enabled=True),
    ])
    mgr = build_manager(cfg, Secrets(_env_file=None))
    assert [c.name for c in mgr.channels] == ["console"]


def test_build_manager_skips_misconfigured_telegram() -> None:
    cfg = Alerts(channels=[
        AlertChannelConfig(kind="telegram", enabled=True),
        AlertChannelConfig(kind="console", enabled=True),
    ])
    mgr = build_manager(cfg, Secrets(_env_file=None))
    assert [c.name for c in mgr.channels] == ["console"]


@pytest.mark.asyncio
async def test_email_uses_resend_api() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"id": "msg_x"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ch = EmailChannel(
            api_key="re_xyz",
            sender="ops@example.com",
            recipients=["a@example.com", "b@example.com"],
            client=client,
        )
        await ch.send("warning", "down", "things broke")

    assert captured["url"].endswith("/emails")
    assert captured["auth"] == "Bearer re_xyz"
    assert "ops@example.com" in captured["body"]
    assert "a@example.com" in captured["body"]
    assert "things broke" in captured["body"]


def test_email_raises_on_missing_secrets() -> None:
    with pytest.raises(ValueError):
        EmailChannel(api_key="", sender="x@y.com", recipients=["a@b.com"])
    with pytest.raises(ValueError):
        EmailChannel(api_key="k", sender="", recipients=["a@b.com"])
    with pytest.raises(ValueError):
        EmailChannel(api_key="k", sender="x@y.com", recipients=[])


@pytest.mark.asyncio
async def test_send_writes_alerts_log_when_session_factory_provided(tmp_path) -> None:
    from sqlalchemy import select

    from src.config import Config, Database, Paths
    from src.db import init_db, make_engine, make_session_factory
    from src.db.models import AlertLog

    cfg = Config(paths=Paths(data_dir=tmp_path), database=Database(url=f"sqlite:///{tmp_path / 'a.db'}"))
    init_db(config=cfg)
    engine = make_engine(cfg)
    session_factory = make_session_factory(engine)

    bound = [_Bound(channel=ConsoleChannel(stream=io.StringIO()), severity_min="info")]
    mgr = ChannelManager(bound, session_factory=session_factory)
    await mgr.send("warning", "test alert", "test body")

    with session_factory() as s:
        rows = s.scalars(select(AlertLog)).all()
    assert len(rows) == 1
    assert rows[0].severity == "warning"
    assert rows[0].title == "test alert"
    assert rows[0].channels == "console"
    assert rows[0].delivered == 1


@pytest.mark.asyncio
async def test_one_failing_channel_does_not_block_others() -> None:
    delivered = []

    class OK(ConsoleChannel):
        name = "ok"

        async def send(self, severity, title, body):
            delivered.append("ok")

    class Bad(ConsoleChannel):
        name = "bad"

        async def send(self, severity, title, body):
            raise RuntimeError("boom")

    bound = [
        _Bound(channel=Bad(stream=io.StringIO()), severity_min="info"),
        _Bound(channel=OK(stream=io.StringIO()), severity_min="info"),
    ]
    mgr = ChannelManager(bound)
    results = await mgr.send("info", "t", "b")
    assert delivered == ["ok"]
    assert any(err is not None for _, err in results)
