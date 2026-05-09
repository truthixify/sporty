from __future__ import annotations

import io

import httpx
import pytest

from src.alerts import ChannelManager, ConsoleChannel
from src.alerts.manager import _Bound
from src.config import Config, Monitor, Thresholds
from src.monitor import evaluate, run_watchdog


def test_evaluate_low_frame_rate() -> None:
    th = Thresholds(min_frames_per_min=10, min_events_per_hour=30)
    alerts = evaluate({"frames_per_min": 1.0, "events_per_hour": 100.0, "last_event_age_s": 5.0}, th)
    assert any(a.title == "capture: low frame rate" for a in alerts)


def test_evaluate_clean_metrics_no_alerts() -> None:
    th = Thresholds(min_frames_per_min=10, min_events_per_hour=30)
    alerts = evaluate({"frames_per_min": 50.0, "events_per_hour": 100.0, "last_event_age_s": 5.0}, th)
    assert alerts == []


def test_evaluate_stale_data_critical() -> None:
    th = Thresholds()
    alerts = evaluate({"frames_per_min": 50.0, "events_per_hour": 100.0, "last_event_age_s": 999.0}, th)
    assert any(a.severity == "critical" for a in alerts)


@pytest.mark.asyncio
async def test_watchdog_dispatches_alerts_on_breach() -> None:
    sent: list[tuple[str, str]] = []

    class Recorder(ConsoleChannel):
        name = "rec"

        async def send(self, severity, title, body):
            sent.append((severity, title))

    cfg = Config(monitor=Monitor(watchdog_interval_seconds=0))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "frames_per_min": 1.0,
            "events_per_hour": 1.0,
            "last_event_age_s": 5.0,
            "last_event_ts": 1.0,
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        mgr = ChannelManager([_Bound(channel=Recorder(stream=io.StringIO()), severity_min="info")])
        ran = await run_watchdog(cfg, mgr, iterations=1, client=client)

    assert ran == 1
    titles = [t for _, t in sent]
    assert "capture: low frame rate" in titles
    assert "capture: low event rate" in titles


@pytest.mark.asyncio
async def test_watchdog_records_metrics_snapshots(tmp_path) -> None:
    from sqlalchemy import select

    from src.config import Config, Database, Paths
    from src.db import init_db, make_engine, make_session_factory
    from src.db.models import MetricsSnapshot

    cfg = Config(
        paths=Paths(data_dir=tmp_path),
        database=Database(url=f"sqlite:///{tmp_path / 'm.db'}"),
        monitor=Monitor(watchdog_interval_seconds=0),
    )
    init_db(config=cfg)
    engine = make_engine(cfg)
    session_factory = make_session_factory(engine)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "frames_per_min": 50.0,
            "events_per_hour": 100.0,
            "last_event_age_s": 5.0,
            "last_event_ts": 1.0,
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        mgr = ChannelManager([_Bound(channel=ConsoleChannel(stream=io.StringIO()), severity_min="info")])
        await run_watchdog(cfg, mgr, iterations=1, client=client, session_factory=session_factory)

    with session_factory() as s:
        rows = s.scalars(select(MetricsSnapshot)).all()
    assert len(rows) == 1
    assert rows[0].frames_per_min == 50.0
    assert rows[0].events_per_hour == 100.0


@pytest.mark.asyncio
async def test_watchdog_alerts_when_metrics_endpoint_fails() -> None:
    sent: list[tuple[str, str, str]] = []

    class Recorder(ConsoleChannel):
        name = "rec"

        async def send(self, severity, title, body):
            sent.append((severity, title, body))

    cfg = Config(monitor=Monitor(watchdog_interval_seconds=0))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="fire")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        mgr = ChannelManager([_Bound(channel=Recorder(stream=io.StringIO()), severity_min="info")])
        await run_watchdog(cfg, mgr, iterations=1, client=client)

    assert any("metrics endpoint unreachable" in t for _, t, _ in sent)
