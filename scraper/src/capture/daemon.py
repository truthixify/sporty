from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from src.capture.filters import should_keep_payload
from src.capture.health import HealthMonitor
from src.capture.journal import JournalWriter, new_session_id
from src.capture.recovery import RecoveryRateLimiter
from src.config import Config


EXIT_CLEAN = 0
EXIT_UNKNOWN = 1
EXIT_SESSION_DEAD = 10
EXIT_NO_IFRAME = 11
EXIT_TOO_MANY_RECOVERIES = 12


async def run_capture(
    cfg: Config,
    *,
    duration_s: int = 0,
    headless: bool | None = None,
) -> int:
    """Run the capture daemon until `duration_s` (0 means forever) or the
    recovery ladder gives up. Returns one of the EXIT_* codes."""
    try:
        from playwright.async_api import (
            BrowserContext,
            Page,
            WebSocket,
            async_playwright,
        )
    except ImportError as exc:
        print(f"capture: playwright not installed: {exc}", file=sys.stderr)
        return EXIT_UNKNOWN

    session_id = new_session_id()
    writer = JournalWriter(captures_dir=cfg.paths.captures_dir, session_id=session_id)
    health = HealthMonitor(
        window_seconds=cfg.capture.recovery.death_window_seconds,
        death_401_threshold=cfg.capture.recovery.death_401_threshold,
        healthy_after_seconds=cfg.capture.recovery.healthy_after_recovery_seconds,
    )
    limiter = RecoveryRateLimiter(max_per_hour=cfg.capture.recovery.max_recoveries_per_hour)
    headless_eff = cfg.capture.headless if headless is None else headless
    parent_url = cfg.capture.parent_url

    def on_ws(ws: "WebSocket") -> None:
        if "virtustec" not in ws.url:
            return
        writer.lifecycle("open", url=ws.url)
        ws.on("close", lambda *_: writer.lifecycle("close", url=ws.url))

        def make_handler(direction: str):
            def handler(payload: str) -> None:
                if not isinstance(payload, str):
                    return
                if not should_keep_payload(payload, cfg.capture.ignore_resources):
                    return
                writer.frame(direction, payload)
                if direction == "in":
                    status = _extract_status(payload)
                    if status is not None:
                        health.record(time.time(), status)
            return handler

        ws.on("framesent", make_handler("out"))
        ws.on("framereceived", make_handler("in"))

    async def attach_to_page(page: "Page") -> None:
        page.on("websocket", on_ws)

    async with async_playwright() as p:
        ctx: "BrowserContext" = await p.chromium.launch_persistent_context(
            user_data_dir=str(cfg.paths.profile_dir),
            headless=headless_eff,
            viewport={"width": 1280, "height": 800},
        )
        try:
            ctx.on("page", lambda np: asyncio.create_task(attach_to_page(np)))
            page = await ctx.new_page()
            await attach_to_page(page)
            for existing in ctx.pages:
                if existing is not page:
                    await attach_to_page(existing)

            try:
                await page.goto(parent_url, wait_until="domcontentloaded", timeout=60_000)
            except Exception as exc:
                print(f"capture: parent goto failed: {exc}", file=sys.stderr)
                return EXIT_UNKNOWN

            iframe_url = await _discover_iframe_url(page, timeout_s=300.0, writer=writer)
            if iframe_url is None:
                print("capture: no virtustec iframe after 5min", file=sys.stderr)
                return EXIT_NO_IFRAME

            start = time.time()
            while True:
                await asyncio.sleep(2)
                if duration_s > 0 and time.time() - start >= duration_s:
                    return EXIT_CLEAN
                if not health.is_dead():
                    continue
                now = time.time()
                if limiter.too_many(now):
                    print("capture: too many recoveries this hour", file=sys.stderr)
                    return EXIT_TOO_MANY_RECOVERIES
                limiter.record_attempt(now)
                ok = await _run_recovery_ladder(page, writer, health, parent_url=parent_url)
                if not ok:
                    print("capture: recovery ladder exhausted", file=sys.stderr)
                    return EXIT_SESSION_DEAD
        except KeyboardInterrupt:
            return EXIT_CLEAN
        except Exception as exc:
            print(f"capture: uncaught {type(exc).__name__}: {exc}", file=sys.stderr)
            return EXIT_UNKNOWN
        finally:
            try:
                await ctx.close()
            finally:
                writer.close()


async def _discover_iframe_url(page, *, timeout_s: float, writer: JournalWriter) -> str | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            url = await page.evaluate(
                "() => { const ifr = Array.from(document.querySelectorAll('iframe'))"
                ".find(f => (f.src||'').includes('virtustec')); return ifr ? ifr.src : null; }"
            )
        except Exception:
            url = None
        if url:
            writer.lifecycle("iframe_url", url=url)
            return url
        await asyncio.sleep(0.5)
    return None


async def _run_recovery_ladder(page, writer, health: HealthMonitor, *, parent_url: str) -> bool:
    if await _try_recovery(page, writer, health, level=1, parent_url=parent_url):
        return True
    if await _try_recovery(page, writer, health, level=2, parent_url=parent_url):
        return True
    if await _try_recovery(page, writer, health, level=3, parent_url=parent_url):
        return True
    return False


async def _try_recovery(page, writer, health: HealthMonitor, *, level: int, parent_url: str) -> bool:
    health.reset()
    try:
        if level == 1:
            await page.evaluate(
                "() => { const f = document.querySelector('iframe[src*=\"virtustec\"]');"
                " if (f) { f.src = f.src; } }"
            )
        elif level == 2:
            await page.reload(wait_until="domcontentloaded", timeout=60_000)
            await _discover_iframe_url(page, timeout_s=15.0, writer=writer)
        elif level == 3:
            await page.goto(parent_url, wait_until="domcontentloaded", timeout=60_000)
            await _discover_iframe_url(page, timeout_s=15.0, writer=writer)
    except Exception:
        return False
    return await _wait_until_healthy(health, timeout_s=health.healthy_after_seconds + 5.0)


async def _wait_until_healthy(health: HealthMonitor, *, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if health.is_healthy_after_recovery():
            return True
        await asyncio.sleep(2)
    return False


def _extract_status(payload: str) -> int | None:
    try:
        d = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict) or d.get("type") != "RESPONSE":
        return None
    res = d.get("res")
    if not isinstance(res, dict):
        return None
    status = res.get("statusCode")
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None
