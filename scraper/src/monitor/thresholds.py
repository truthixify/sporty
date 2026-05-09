from __future__ import annotations

import time
from dataclasses import dataclass

from src.alerts.base import Severity
from src.config import Thresholds


@dataclass(frozen=True)
class ThresholdAlert:
    severity: Severity
    title: str
    body: str


class ThresholdEvaluator:
    """Stateful evaluator that holds the per-condition "first time we saw this
    breach" timestamp so we only fire once the spec's sustain window has
    elapsed. The state survives across watchdog ticks so a transient dip
    doesn't page anyone."""

    def __init__(self, thresholds: Thresholds) -> None:
        self._thresholds = thresholds
        self._first_breach: dict[str, float] = {}

    def evaluate(self, metrics: dict, *, now: float | None = None) -> list[ThresholdAlert]:
        when = time.time() if now is None else now
        alerts: list[ThresholdAlert] = []

        fpm = metrics.get("frames_per_min")
        if fpm is not None:
            self._sustain_check(
                key="capture: low frame rate",
                breached=fpm < self._thresholds.min_frames_per_min,
                sustain_s=self._thresholds.sustain_frames_seconds,
                now=when,
                build=lambda elapsed: ThresholdAlert(
                    severity="warning",
                    title="capture: low frame rate",
                    body=(
                        f"frames_per_min={fpm:.1f} below threshold "
                        f"{self._thresholds.min_frames_per_min} for {int(elapsed)}s. "
                        f"WS is connected but barely flowing. Most likely the iframe "
                        f"never reached the virtuals page (login expired, or the "
                        f"browser landed on a non-virtuals page). Check journalctl "
                        f"-u scraper-capture and consider re-running bootstrap_login.py."
                    ),
                ),
                out=alerts,
            )

        eph = metrics.get("events_per_hour")
        if eph is not None:
            self._sustain_check(
                key="capture: low event rate",
                breached=eph < self._thresholds.min_events_per_hour,
                sustain_s=self._thresholds.sustain_events_seconds,
                now=when,
                build=lambda elapsed: ThresholdAlert(
                    severity="warning",
                    title="capture: low event rate",
                    body=(
                        f"events_per_hour={eph:.1f} below threshold "
                        f"{self._thresholds.min_events_per_hour} for {int(elapsed)}s. "
                        f"Frames are flowing but few are /event/data. Either an "
                        f"off-peak hour, or the parser stopped advancing watermarks."
                    ),
                ),
                out=alerts,
            )

        age = metrics.get("last_event_age_s")
        if age is not None and age > self._thresholds.stale_data_seconds:
            alerts.append(ThresholdAlert(
                severity="critical",
                title="capture: stale data",
                body=(
                    f"no event captured in {int(age)}s. "
                    f"Either capture has stopped writing the journal, or the "
                    f"parser has stopped draining it. Check `scraper status` and "
                    f"that both processes are running."
                ),
            ))

        return alerts

    def _sustain_check(
        self, *, key: str, breached: bool, sustain_s: float, now: float, build, out,
    ) -> None:
        if breached:
            first = self._first_breach.setdefault(key, now)
            if now - first >= sustain_s:
                out.append(build(now - first))
        else:
            self._first_breach.pop(key, None)


# Backwards-compatible function-style API used by older tests; constructs a
# fresh evaluator with no sustain memory so a single bad sample fires
# immediately (the spec's sustain windows only matter to the live watchdog).
def evaluate(metrics: dict, thresholds: Thresholds) -> list[ThresholdAlert]:
    """Inspect a /metrics/capture payload and return any breached thresholds.

    This is the stateless variant: it triggers immediately on a single bad
    sample (no sustain). The watchdog uses `ThresholdEvaluator` for sustain
    semantics; this helper is for ad-hoc evaluation and unit tests.
    """
    alerts: list[ThresholdAlert] = []

    fpm = metrics.get("frames_per_min")
    if fpm is not None and fpm < thresholds.min_frames_per_min:
        alerts.append(ThresholdAlert(
            severity="warning",
            title="capture: low frame rate",
            body=f"frames_per_min={fpm:.1f} < threshold={thresholds.min_frames_per_min}",
        ))

    eph = metrics.get("events_per_hour")
    if eph is not None and eph < thresholds.min_events_per_hour:
        alerts.append(ThresholdAlert(
            severity="warning",
            title="capture: low event rate",
            body=f"events_per_hour={eph:.1f} < threshold={thresholds.min_events_per_hour}",
        ))

    age = metrics.get("last_event_age_s")
    if age is not None and age > thresholds.stale_data_seconds:
        alerts.append(ThresholdAlert(
            severity="critical",
            title="capture: stale data",
            body=f"no event captured in {int(age)}s",
        ))

    return alerts
