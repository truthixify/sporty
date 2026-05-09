from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class ResponsePair:
    """A matched JSON-RPC REQUEST/RESPONSE pair within a single WS lifetime."""

    request_ts: float
    response_ts: float
    xs: int
    resource: str
    method: str
    status_code: int
    body: Any
    raw_request: dict[str, Any]
    raw_response: dict[str, Any]


class Pairer:
    """Match REQUEST→RESPONSE frames by `xs`, scoped to one WS lifetime.

    Call `reset()` on every WS open/close marker; that clears in-flight requests
    so a stale `xs` from a previous connection cannot mis-pair against a new one.
    """

    def __init__(self) -> None:
        self._pending: dict[int, tuple[float, dict[str, Any]]] = {}

    def reset(self) -> None:
        self._pending.clear()

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def feed_request(self, ts: float, payload: dict[str, Any]) -> None:
        xs = payload.get("xs")
        req = payload.get("req")
        if not isinstance(xs, int) or not isinstance(req, dict):
            return
        self._pending[xs] = (ts, req)

    def feed_response(self, ts: float, payload: dict[str, Any]) -> ResponsePair | None:
        xs = payload.get("xs")
        res = payload.get("res")
        if not isinstance(xs, int) or not isinstance(res, dict):
            return None
        match = self._pending.pop(xs, None)
        if match is None:
            return None
        req_ts, req = match
        status = res.get("statusCode")
        try:
            status_int = int(status) if status is not None else 0
        except (TypeError, ValueError):
            status_int = 0
        return ResponsePair(
            request_ts=req_ts,
            response_ts=ts,
            xs=xs,
            resource=str(req.get("resource", "")),
            method=str(req.get("method", "")),
            status_code=status_int,
            body=res.get("body"),
            raw_request=req,
            raw_response=res,
        )
