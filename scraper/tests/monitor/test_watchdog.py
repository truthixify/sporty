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


def test_threshold_evaluator_sustain_window_holds_alert() -> None:
    from src.monitor import ThresholdEvaluator

    th = Thresholds(min_frames_per_min=10, sustain_frames_seconds=300.0)
    e = ThresholdEvaluator(th)
    metrics = {"frames_per_min": 1.0, "events_per_hour": 100.0, "last_event_age_s": 5.0}

    # Sustained breach not yet long enough
    assert e.evaluate(metrics, now=100.0) == []
    assert e.evaluate(metrics, now=399.0) == []
    # Cross the 5min threshold
    alerts = e.evaluate(metrics, now=401.0)
    assert any(a.title == "capture: low frame rate" for a in alerts)


def test_threshold_evaluator_sustain_resets_when_cleared() -> None:
    from src.monitor import ThresholdEvaluator

    th = Thresholds(min_frames_per_min=10, sustain_frames_seconds=10.0)
    e = ThresholdEvaluator(th)

    e.evaluate({"frames_per_min": 1.0}, now=0.0)
    e.evaluate({"frames_per_min": 50.0}, now=5.0)  # clears
    e.evaluate({"frames_per_min": 1.0}, now=8.0)
    # Sustain restarts at 8s, so at t=15 (only 7s since restart) no alert
    assert e.evaluate({"frames_per_min": 1.0}, now=15.0) == []
    # At t=20 (12s since restart) it fires
    alerts = e.evaluate({"frames_per_min": 1.0}, now=20.0)
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

    cfg = Config(monitor=Monitor(
        watchdog_interval_seconds=0,
        thresholds=Thresholds(
            min_frames_per_min=10, min_events_per_hour=30,
            sustain_frames_seconds=0, sustain_events_seconds=0,
        ),
    ))

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
async def test_watchdog_alerts_on_capture_session_recovery_exit(tmp_path) -> None:
    from src.config import Config, Database, Monitor, Paths
    from src.db import init_db, make_engine, make_session_factory
    from src.db.models import CaptureSession

    sent: list[tuple[str, str]] = []

    class Recorder(ConsoleChannel):
        name = "rec"

        async def send(self, severity, title, body):
            sent.append((severity, title))

    cfg = Config(
        paths=Paths(data_dir=tmp_path),
        database=Database(url=f"sqlite:///{tmp_path / 's.db'}"),
        monitor=Monitor(watchdog_interval_seconds=0),
    )
    init_db(config=cfg)
    engine = make_engine(cfg)
    session_factory = make_session_factory(engine)
    with session_factory() as s:
        s.add(CaptureSession(
            session_id="sess-bad", started_ts=1.0, ended_ts=2.0,
            end_reason="too_many_recoveries", frames_in=10, frames_out=5, error_count=6,
        ))
        s.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "frames_per_min": 50.0, "events_per_hour": 100.0,
            "last_event_age_s": 5.0, "last_event_ts": 1.0,
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        mgr = ChannelManager([_Bound(channel=Recorder(stream=io.StringIO()), severity_min="info")])
        await run_watchdog(cfg, mgr, iterations=1, client=client, session_factory=session_factory)

    assert any("session ended" in t for _, t in sent)


@pytest.mark.asyncio
async def test_watchdog_alerts_on_stale_parser_watermark(tmp_path) -> None:
    from src.config import Config, Database, Monitor, Paths, Thresholds
    from src.db import init_db, make_engine, make_session_factory
    from src.db.models import ParseWatermark

    sent: list[tuple[str, str]] = []

    class Recorder(ConsoleChannel):
        name = "rec"

        async def send(self, severity, title, body):
            sent.append((severity, title))

    cfg = Config(
        paths=Paths(data_dir=tmp_path),
        database=Database(url=f"sqlite:///{tmp_path / 'w.db'}"),
        monitor=Monitor(
            watchdog_interval_seconds=0,
            thresholds=Thresholds(parser_watermark_stale_seconds=60),
        ),
    )
    init_db(config=cfg)
    engine = make_engine(cfg)
    session_factory = make_session_factory(engine)
    with session_factory() as s:
        s.add(ParseWatermark(
            journal_file="/tmp/old.jsonl", last_offset=0, last_line=0,
            parsed_count=0, last_run_ts=1.0,  # ancient
        ))
        s.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "frames_per_min": 50.0, "events_per_hour": 100.0,
            "last_event_age_s": 5.0, "last_event_ts": 1.0,
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        mgr = ChannelManager([_Bound(channel=Recorder(stream=io.StringIO()), severity_min="info")])
        await run_watchdog(cfg, mgr, iterations=1, client=client, session_factory=session_factory)

    assert any("watermark stale" in t for _, t in sent)


@pytest.mark.asyncio
async def test_watchdog_does_not_refire_same_title_within_cooldown() -> None:
    from src.config import Config, Database, Monitor, Paths, Thresholds
    from src.db import init_db, make_engine, make_session_factory

    sent: list[tuple[str, str, str]] = []

    class Recorder(ConsoleChannel):
        name = "rec"

        async def send(self, severity, title, body):
            sent.append((severity, title, body))

    cfg = Config(
        monitor=Monitor(
            watchdog_interval_seconds=0,
            thresholds=Thresholds(
                min_frames_per_min=10, min_events_per_hour=30,
                sustain_frames_seconds=0, sustain_events_seconds=0,
                stale_data_seconds=600,
                alert_refire_seconds=1800,
            ),
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "frames_per_min": 1.0, "events_per_hour": 1.0,
            "last_event_age_s": 5.0, "last_event_ts": 1.0,
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        mgr = ChannelManager([_Bound(channel=Recorder(stream=io.StringIO()), severity_min="info")])
        await run_watchdog(cfg, mgr, iterations=5, client=client)

    # 2 unique titles ("low frame rate" + "low event rate"), each fired once
    titles = [t for _, t, _ in sent]
    assert titles.count("capture: low frame rate") == 1
    assert titles.count("capture: low event rate") == 1


@pytest.mark.asyncio
async def test_watchdog_refires_after_resolution_and_re_breach() -> None:
    from src.config import Config, Monitor, Thresholds

    sent: list[str] = []

    class Recorder(ConsoleChannel):
        name = "rec"

        async def send(self, severity, title, body):
            sent.append(title)

    cfg = Config(
        monitor=Monitor(
            watchdog_interval_seconds=0,
            thresholds=Thresholds(
                min_frames_per_min=10, min_events_per_hour=30,
                sustain_frames_seconds=0, sustain_events_seconds=0,
                stale_data_seconds=600,
                alert_refire_seconds=1800,
            ),
        ),
    )

    # Iteration 1: bad. Iteration 2: good (clears). Iteration 3: bad again.
    counter = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["i"] += 1
        if counter["i"] in (1, 3):
            return httpx.Response(200, json={
                "frames_per_min": 1.0, "events_per_hour": 100.0,
                "last_event_age_s": 5.0, "last_event_ts": 1.0,
            })
        return httpx.Response(200, json={
            "frames_per_min": 50.0, "events_per_hour": 100.0,
            "last_event_age_s": 5.0, "last_event_ts": 1.0,
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        mgr = ChannelManager([_Bound(channel=Recorder(stream=io.StringIO()), severity_min="info")])
        await run_watchdog(cfg, mgr, iterations=3, client=client)

    assert sent.count("capture: low frame rate") == 2


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
