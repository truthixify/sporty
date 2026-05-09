"""Open a headed Chromium against the SportyBet virtuals page so the operator
can complete login (CAPTCHA, geo, account password) once. The persistent
profile is saved under `paths.profile_dir`; subsequent capture runs reuse it.

Usage: `uv run python scripts/bootstrap_login.py`
"""

from __future__ import annotations

import asyncio
import sys

from src.config import load_config


async def main() -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        print(f"playwright missing: {exc}", file=sys.stderr)
        return 2

    cfg = load_config()
    cfg.paths.profile_dir.mkdir(parents=True, exist_ok=True)
    print(f"profile dir: {cfg.paths.profile_dir}")
    print(f"opening: {cfg.capture.parent_url}")
    print("complete login in the browser; close the window when ready.")

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(cfg.paths.profile_dir),
            headless=False,
            viewport={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()
        await page.goto(cfg.capture.parent_url, wait_until="domcontentloaded", timeout=60_000)
        try:
            await page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        finally:
            await ctx.close()

    print("login profile saved. capture daemon can now run headless.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
