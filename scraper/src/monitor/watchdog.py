from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

import httpx
from sqlalchemy.orm import Session, sessionmaker

from src.alerts import ChannelManager
from src.config import Config
from src.monitor.thresholds import ThresholdAlert, evaluate


log = logging.getLogger(__name__)


async def run_watchdog(
    cfg: Config,
    manager: ChannelManager,
    *,
    iterations: int | None = None,
    client: httpx.AsyncClient | None = None,
    session_factory: Callable[[], Session] | sessionmaker[Session] | None = None,
) -> int:
    """Poll /metrics/capture every `watchdog_interval_seconds`, dispatch alerts
    on breached thresholds, and (if `session_factory` is provided) record each
    sample to `metrics_snapshots` for retrospective debugging.

    `iterations=None` runs until cancelled (typical daemon mode); a positive
    int caps the loop for tests. Returns the number of iterations that ran.
    """
    base = f"http://{cfg.api.host}:{cfg.api.port}"
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=10.0)
    last_alerts: dict[str, ThresholdAlert] = {}
    ran = 0

    try:
        while iterations is None or ran < iterations:
            ran += 1
            try:
                r = await http.get(f"{base}/metrics/capture")
                r.raise_for_status()
                metrics = r.json()
            except Exception as exc:
                log.warning("watchdog: metrics fetch failed: %s", exc)
                await manager.send(
                    "critical", "watchdog: metrics endpoint unreachable", str(exc)
                )
                await asyncio.sleep(cfg.monitor.watchdog_interval_seconds)
                continue

            _record_snapshot(session_factory, metrics)

            for alert in evaluate(metrics, cfg.monitor.thresholds):
                key = alert.title
                if last_alerts.get(key) == alert:
                    continue
                last_alerts[key] = alert
                await manager.send(alert.severity, alert.title, alert.body)

            if iterations is None or ran < iterations:
                await asyncio.sleep(cfg.monitor.watchdog_interval_seconds)
    finally:
        if own_client:
            await http.aclose()
    return ran


def _record_snapshot(
    session_factory: Callable[[], Session] | sessionmaker[Session] | None,
    metrics: dict,
) -> None:
    if session_factory is None:
        return
    try:
        from src.db.models import MetricsSnapshot

        with session_factory() as session:
            session.add(MetricsSnapshot(
                snapshot_ts=time.time(),
                frames_per_min=metrics.get("frames_per_min"),
                events_per_hour=metrics.get("events_per_hour"),
                error_rate=None,
                raw=metrics,
            ))
            session.commit()
    except Exception as exc:
        log.warning("metrics_snapshots write failed: %s", exc)
