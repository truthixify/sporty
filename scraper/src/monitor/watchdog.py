from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.alerts import ChannelManager
from src.config import Config
from src.monitor.thresholds import ThresholdAlert, ThresholdEvaluator


log = logging.getLogger(__name__)

# Capture session end_reasons that should always page someone, mapped from
# the daemon's exit codes (see src/capture/daemon.py).
_RECOVERY_END_REASONS = {
    "recovery_exhausted",      # exit 10
    "no_iframe",               # exit 11
    "too_many_recoveries",     # exit 12
}


async def run_watchdog(
    cfg: Config,
    manager: ChannelManager,
    *,
    iterations: int | None = None,
    client: httpx.AsyncClient | None = None,
    session_factory: Callable[[], Session] | sessionmaker[Session] | None = None,
) -> int:
    """Poll /metrics/capture every `watchdog_interval_seconds`, dispatch alerts
    for breached thresholds (with sustain windows), unhealthy capture session
    exits, and stale parser watermarks. If `session_factory` is provided each
    sample is also recorded to `metrics_snapshots`.

    `iterations=None` runs until cancelled; a positive int caps the loop for
    tests. Returns the number of iterations that ran.
    """
    base = f"http://{cfg.api.host}:{cfg.api.port}"
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=10.0)
    seen_sessions: set[str] = set()
    evaluator = ThresholdEvaluator(cfg.monitor.thresholds)
    # Per-title state: when we first fired this alert and when we last fired
    # it. Used to suppress same-title spam (the body changes every tick
    # because it includes the elapsed seconds).
    active: dict[str, dict[str, float]] = {}
    refire_interval_s = cfg.monitor.thresholds.alert_refire_seconds
    heartbeat_interval = cfg.monitor.heartbeat_interval_seconds
    last_heartbeat_ts = 0.0
    ran = 0
    sent_startup = False

    try:
        while iterations is None or ran < iterations:
            ran += 1
            now = time.time()
            try:
                r = await http.get(f"{base}/metrics/capture")
                r.raise_for_status()
                metrics = r.json()
            except Exception as exc:
                log.warning("watchdog: metrics fetch failed: %s", exc)
                await _maybe_send(
                    manager, active, now, refire_interval_s,
                    ThresholdAlert(
                        severity="critical",
                        title="watchdog: metrics endpoint unreachable",
                        body=str(exc),
                    ),
                )
                await asyncio.sleep(cfg.monitor.watchdog_interval_seconds)
                continue

            _record_snapshot(session_factory, metrics)

            if not sent_startup:
                sent_startup = True
                last_heartbeat_ts = now
                hb_note = (
                    f"heartbeat every {heartbeat_interval}s is enabled."
                    if heartbeat_interval > 0
                    else "you'll only get more messages on threshold breaches."
                )
                await manager.send(
                    "info",
                    "scraper: monitoring started",
                    (
                        f"watchdog is up. polling every {cfg.monitor.watchdog_interval_seconds}s. "
                        f"current state: events_per_hour={metrics.get('events_per_hour')}, "
                        f"frames_per_min={metrics.get('frames_per_min')}, "
                        f"last_event_age_s={metrics.get('last_event_age_s')}. "
                        f"{hb_note}"
                    ),
                )

            if heartbeat_interval > 0 and now - last_heartbeat_ts >= heartbeat_interval:
                last_heartbeat_ts = now
                await _send_heartbeat(manager, metrics, session_factory)

            alerts = list(evaluator.evaluate(metrics))
            alerts.extend(_db_alerts(session_factory, cfg, seen_sessions))

            current_titles = {a.title for a in alerts}
            for alert in alerts:
                await _maybe_send(manager, active, now, refire_interval_s, alert)

            # Auto-clear titles whose conditions have resolved AND fire a
            # positive "resolved" alert so the operator knows the system
            # came back without having to hand-poll.
            for title in list(active):
                if title not in current_titles:
                    state = active.pop(title)
                    duration = int(now - state["first_ts"])
                    await manager.send(
                        "info",
                        f"resolved: {title}",
                        f"the condition above cleared after {duration}s.",
                    )

            if iterations is None or ran < iterations:
                await asyncio.sleep(cfg.monitor.watchdog_interval_seconds)
    finally:
        if own_client:
            await http.aclose()
    return ran


async def _send_heartbeat(
    manager: ChannelManager,
    metrics: dict,
    session_factory: Callable[[], Session] | sessionmaker[Session] | None,
) -> None:
    """Periodic 'still alive' message with current metrics and DB row counts.
    Distinct from the startup alert (which fires once at process start)."""
    counts = ""
    if session_factory is not None:
        try:
            from sqlalchemy import func, select

            from src.db.models import Event, Schema, Standing

            with session_factory() as s:
                ev = s.scalar(select(func.count()).select_from(Event)) or 0
                sc = s.scalar(select(func.count()).select_from(Schema)) or 0
                st = s.scalar(select(func.count()).select_from(Standing)) or 0
                counts = f" | db: events={ev} schemas={sc} standings={st}"
        except Exception as exc:
            log.warning("heartbeat: db count query failed: %s", exc)

    fpm = metrics.get("frames_per_min")
    eph = metrics.get("events_per_hour")
    age = metrics.get("last_event_age_s")
    body = (
        f"events_per_hour={eph} frames_per_min={fpm:.1f if isinstance(fpm,(int,float)) else fpm} "
        f"last_event_age_s={age}{counts}"
    )
    # Prefer formatting safely without nested f-string conditionals
    fpm_s = f"{fpm:.1f}" if isinstance(fpm, (int, float)) else str(fpm)
    body = (
        f"events_per_hour={eph} frames_per_min={fpm_s} "
        f"last_event_age_s={age}{counts}"
    )
    await manager.send("info", "scraper: heartbeat", body)


async def _maybe_send(
    manager: ChannelManager,
    active: dict[str, dict[str, float]],
    now: float,
    refire_interval_s: float,
    alert: ThresholdAlert,
) -> None:
    """Send the alert iff we haven't fired the same title in the last
    `refire_interval_s` seconds. On re-fire, prefix the body with how long
    it's been firing so the operator can see the trend without spam."""
    state = active.get(alert.title)
    if state is None:
        active[alert.title] = {"first_ts": now, "last_ts": now}
        await manager.send(alert.severity, alert.title, alert.body)
        return
    if now - state["last_ts"] < refire_interval_s:
        return
    state["last_ts"] = now
    elapsed = now - state["first_ts"]
    body = f"still firing after {int(elapsed)}s. {alert.body}"
    await manager.send(alert.severity, alert.title, body)


def _db_alerts(
    session_factory: Callable[[], Session] | sessionmaker[Session] | None,
    cfg: Config,
    seen_sessions: set[str],
) -> list[ThresholdAlert]:
    """Pull alerts that come from DB state (capture session exits, stale
    parser watermarks). Returns an empty list if no session_factory is bound
    or if anything goes wrong (we never want a flaky DB to silence the rest of
    the watchdog)."""
    if session_factory is None:
        return []
    out: list[ThresholdAlert] = []
    try:
        from src.db.models import CaptureSession, ParseWatermark

        with session_factory() as session:
            for s in session.scalars(
                select(CaptureSession)
                .where(CaptureSession.end_reason.in_(_RECOVERY_END_REASONS))
                .order_by(CaptureSession.started_ts.desc())
                .limit(20)
            ):
                if s.session_id in seen_sessions:
                    continue
                seen_sessions.add(s.session_id)
                hint = _end_reason_hint(s.end_reason)
                out.append(ThresholdAlert(
                    severity="critical",
                    title=f"capture: session ended ({s.end_reason})",
                    body=(
                        f"session_id={s.session_id} "
                        f"frames_in={s.frames_in} frames_out={s.frames_out} "
                        f"errors={s.error_count}. {hint}"
                    ),
                ))

            latest_run = session.scalar(select(func.max(ParseWatermark.last_run_ts)))
            if latest_run is not None:
                age = time.time() - float(latest_run)
                if age > cfg.monitor.thresholds.parser_watermark_stale_seconds:
                    out.append(ThresholdAlert(
                        severity="warning",
                        title="parse: watermark stale",
                        body=(
                            f"no parser run in {int(age)}s. "
                            f"The systemd timer or `scraper dev`'s parse loop "
                            f"is not advancing watermarks. Check the parser "
                            f"process and run `scraper parse` by hand."
                        ),
                    ))
    except Exception as exc:
        log.warning("watchdog: db_alerts query failed: %s", exc)
    return out


def _end_reason_hint(reason: str | None) -> str:
    if reason == "recovery_exhausted":
        return ("Daemon exhausted its L1/L2/L3 recovery ladder. Session is dead; "
                "the wrapper script will restart it after 30s.")
    if reason == "no_iframe":
        return ("Daemon never found a virtustec iframe. Login probably expired or "
                "the page layout changed. Re-run scripts/bootstrap_login.py.")
    if reason == "too_many_recoveries":
        return ("Daemon hit the 6-recoveries-per-hour budget. Something upstream "
                "is broken (Cloudflare, account, region). Wrapper will back off 30 min.")
    return ""


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
