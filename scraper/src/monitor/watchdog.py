from __future__ import annotations

import asyncio
import logging

import httpx

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
) -> int:
    """Poll /metrics/capture every `watchdog_interval_seconds`, dispatch alerts
    on breached thresholds. `iterations=None` runs until cancelled (typical
    daemon mode); a positive int caps the loop for tests.

    Returns the number of iterations that actually ran.
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
