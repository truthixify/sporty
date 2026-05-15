"""One-shot probe: launch Playwright headless Chromium against the SportyBet
virtuals page and dump enough info about what got rendered to tell us why the
capture daemon's iframe discovery is failing.

Run:
    uv run python scripts/probe_page.py

Useful when capture writes nothing to the journal — that means either no WS
to virtustec opened (the iframe wasn't loaded) or the page itself didn't
load. This script answers the question.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from src.config import load_config


PROFILE = Path("/tmp/probe-profile")


async def main() -> int:
    # Prefer patchright (anti-fingerprint patches built in) so the probe
    # mirrors what the daemon will actually do.
    try:
        from patchright.async_api import async_playwright
        print("=== using patchright (with anti-fingerprint patches)")
    except ImportError:
        try:
            from playwright.async_api import async_playwright
            print("=== using vanilla playwright (patchright not installed)")
        except ImportError:
            print("playwright not installed in this venv")
            return 2

    cfg = load_config()
    url = cfg.capture.parent_url

    shutil.rmtree(PROFILE, ignore_errors=True)
    PROFILE.mkdir()

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=True,
            viewport={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()
        try:
            print(f"=== probing: {url}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            except Exception as exc:
                print(f"=== goto failed: {exc}")
                return 3
            await asyncio.sleep(10)

            print(f"=== final url: {page.url}")
            print(f"=== title:     {await page.title()}")

            html = await page.content()
            print(f"=== html length: {len(html)} chars")

            frames = await page.evaluate(
                "Array.from(document.querySelectorAll('iframe'))"
                ".map(f => ({src: f.src, id: f.id, cls: f.className}))"
            )
            print(f"=== iframes ({len(frames)}):")
            for f in frames:
                print(f"  {f}")

            lower = html.lower()
            print("=== keyword hits in HTML:")
            for kw in (
                "virtustec", "virtual", "captcha", "robot", "verify",
                "cloudflare", "forbidden", "access denied", "not available",
                "region", "geo", "blocked", "cookie", "consent",
            ):
                c = lower.count(kw)
                if c > 0:
                    print(f"  {kw:>15}  {c}")

            body_idx = lower.find("<body")
            snippet = html[body_idx:body_idx + 2500] if body_idx >= 0 else html[:2500]
            print("=== first ~2500 chars of body:")
            print(snippet)
        finally:
            await ctx.close()
            shutil.rmtree(PROFILE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
