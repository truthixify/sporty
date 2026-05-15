from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from src.capture.browser import (
    discover_iframe_url,
    prepare_profile_for_launch,
    reload_iframe,
    reload_parent,
    renavigate_parent,
)
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
    # Prefer patchright (a maintained fork of Playwright with built-in
    # anti-fingerprint patches: navigator.webdriver, canvas/webgl/audio
    # spoofing, etc.) since SportyBet's reCAPTCHA Enterprise blocks vanilla
    # headless Chromium. Falls back to plain playwright if patchright isn't
    # installed.
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        try:
            from playwright.async_api import async_playwright
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

    started_ts = time.time()
    frames_in = frames_out = error_count = 0

    def on_ws(ws) -> None:
        if "virtustec" not in ws.url:
            return
        writer.lifecycle("open", url=ws.url)
        ws.on("close", lambda *_: writer.lifecycle("close", url=ws.url))

        def make_handler(direction: str):
            def handler(payload: str) -> None:
                nonlocal frames_in, frames_out
                if not isinstance(payload, str):
                    return
                if not should_keep_payload(payload, cfg.capture.ignore_resources):
                    return
                writer.frame(direction, payload)
                if direction == "in":
                    frames_in += 1
                    status = _extract_status(payload)
                    if status is not None:
                        health.record(time.time(), status)
                else:
                    frames_out += 1
            return handler

        ws.on("framesent", make_handler("out"))
        ws.on("framereceived", make_handler("in"))

    async def attach_to_page(page) -> None:
        page.on("websocket", on_ws)

    end_reason = "ok"
    exit_code = EXIT_CLEAN

    # Pick the profile dir. SportyBet's /virtual page works fully anonymously
    # so the default is an ephemeral tmp dir that gets nuked on shutdown - no
    # SingletonLock / crash-marker carryover between runs. Set
    # `capture.persistent_profile: true` in config.yaml to keep using the
    # configured profile_dir (e.g. if you ran bootstrap_login for some reason
    # and need that state to stick around).
    if cfg.capture.persistent_profile:
        profile_dir = cfg.paths.profile_dir
        profile_dir.mkdir(parents=True, exist_ok=True)
        prepare_profile_for_launch(profile_dir)
        ephemeral_profile_path: Path | None = None
    else:
        ephemeral_profile_path = Path(tempfile.mkdtemp(prefix="scraper-chromium-"))
        profile_dir = ephemeral_profile_path

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
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
                end_reason = "parent_goto_failed"
                exit_code = EXIT_UNKNOWN
                return exit_code

            iframe_url = await discover_iframe_url(page, timeout_s=300.0)
            if iframe_url is None:
                print("capture: no virtustec iframe after 5min", file=sys.stderr)
                end_reason = "no_iframe"
                exit_code = EXIT_NO_IFRAME
                return exit_code
            writer.lifecycle("iframe_url", url=iframe_url)

            start = time.time()
            while True:
                await asyncio.sleep(2)
                if duration_s > 0 and time.time() - start >= duration_s:
                    end_reason = "duration_reached"
                    exit_code = EXIT_CLEAN
                    return exit_code
                if not health.is_dead():
                    continue
                now = time.time()
                if limiter.too_many(now):
                    print("capture: too many recoveries this hour", file=sys.stderr)
                    end_reason = "too_many_recoveries"
                    exit_code = EXIT_TOO_MANY_RECOVERIES
                    return exit_code
                limiter.record_attempt(now)
                error_count += 1
                ok = await _run_recovery_ladder(page, writer, health, parent_url=parent_url)
                if not ok:
                    print("capture: recovery ladder exhausted", file=sys.stderr)
                    end_reason = "recovery_exhausted"
                    exit_code = EXIT_SESSION_DEAD
                    return exit_code
        except KeyboardInterrupt:
            end_reason = "sigint"
            exit_code = EXIT_CLEAN
            return exit_code
        except Exception as exc:
            print(f"capture: uncaught {type(exc).__name__}: {exc}", file=sys.stderr)
            end_reason = f"uncaught:{type(exc).__name__}"
            exit_code = EXIT_UNKNOWN
            return exit_code
        finally:
            try:
                await ctx.close()
            finally:
                writer.close()
                _record_session(
                    cfg,
                    session_id=session_id,
                    started_ts=started_ts,
                    end_reason=end_reason,
                    frames_in=frames_in,
                    frames_out=frames_out,
                    error_count=error_count,
                )
                if ephemeral_profile_path is not None:
                    shutil.rmtree(ephemeral_profile_path, ignore_errors=True)


def _record_session(
    cfg: Config,
    *,
    session_id: str,
    started_ts: float,
    end_reason: str,
    frames_in: int,
    frames_out: int,
    error_count: int,
) -> None:
    """Best-effort write of a CaptureSession row at daemon shutdown so the API
    can report on past sessions. Failures here are logged and swallowed; we
    never want a DB write to mask the real exit code."""
    try:
        from src.db import make_engine, make_session_factory
        from src.db.models import CaptureSession

        engine = make_engine(cfg)
        Session = make_session_factory(engine)
        with Session() as session:
            session.merge(CaptureSession(
                session_id=session_id,
                started_ts=started_ts,
                ended_ts=time.time(),
                end_reason=end_reason,
                frames_in=frames_in,
                frames_out=frames_out,
                error_count=error_count,
            ))
            session.commit()
    except Exception as exc:
        print(f"capture: failed to record session row: {exc}", file=sys.stderr)


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
            await reload_iframe(page)
        elif level == 2:
            await reload_parent(page)
            url = await discover_iframe_url(page, timeout_s=15.0)
            if url:
                writer.lifecycle("iframe_url", url=url)
        elif level == 3:
            await renavigate_parent(page, parent_url)
            url = await discover_iframe_url(page, timeout_s=15.0)
            if url:
                writer.lifecycle("iframe_url", url=url)
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
