"""Playwright wrappers used by the capture daemon. Pulled out of `daemon.py`
so that file can stay focused on orchestration (run loop, recovery decisions,
exit codes) and so the browser-specific code can be replaced or stubbed in
tests."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Protocol


class _Page(Protocol):
    url: str
    async def evaluate(self, expr: str) -> Any: ...
    async def goto(self, url: str, **kw: Any) -> Any: ...
    async def reload(self, **kw: Any) -> Any: ...
    def on(self, event: str, handler: Callable) -> None: ...


_FIND_IFRAME_JS = (
    "() => {"
    " const ifr = Array.from(document.querySelectorAll('iframe'))"
    "  .find(f => (f.src||'').includes('virtustec'));"
    " return ifr ? ifr.src : null; }"
)
_RELOAD_IFRAME_JS = (
    "() => {"
    " const f = document.querySelector('iframe[src*=\"virtustec\"]');"
    " if (f) { f.src = f.src; } }"
)


async def discover_iframe_url(page: _Page, *, timeout_s: float = 300.0) -> str | None:
    """Poll the page DOM until an `<iframe>` whose src points at virtustec
    appears (the iframe is injected by SportyBet's JS after parent load).
    Returns the src URL, or None on timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            url = await page.evaluate(_FIND_IFRAME_JS)
        except Exception:
            url = None
        if url:
            return url
        await asyncio.sleep(0.5)
    return None


async def reload_iframe(page: _Page) -> None:
    """L1 recovery: poke the iframe's src to force it to reload in place
    without touching the parent page or the persistent profile state."""
    await page.evaluate(_RELOAD_IFRAME_JS)


async def reload_parent(page: _Page) -> None:
    """L2 recovery: refresh the parent page (which re-runs SportyBet's JS and
    re-injects the iframe with a fresh `onlineHash`)."""
    await page.reload(wait_until="domcontentloaded", timeout=60_000)


async def renavigate_parent(page: _Page, parent_url: str) -> None:
    """L3 recovery: full re-navigation to the parent URL from scratch."""
    await page.goto(parent_url, wait_until="domcontentloaded", timeout=60_000)
