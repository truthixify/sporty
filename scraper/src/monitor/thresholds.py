from __future__ import annotations

from dataclasses import dataclass

from src.alerts.base import Severity
from src.config import Thresholds


@dataclass(frozen=True)
class ThresholdAlert:
    severity: Severity
    title: str
    body: str


def evaluate(metrics: dict, thresholds: Thresholds) -> list[ThresholdAlert]:
    """Inspect a /metrics/capture payload and return any breached thresholds."""
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
    if age is not None and age > 600:
        alerts.append(ThresholdAlert(
            severity="critical",
            title="capture: stale data",
            body=f"no event captured in {int(age)}s",
        ))

    return alerts
