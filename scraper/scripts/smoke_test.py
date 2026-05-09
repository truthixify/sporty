"""End-to-end smoke check used during VPS bring-up. Touches each component:
db, parse (against any existing journals), api boot, alert delivery.

Usage: `uv run python scripts/smoke_test.py`
"""

from __future__ import annotations

import asyncio
import sys

import httpx

from src.alerts import build_manager
from src.config import load_config, load_secrets
from src.db import init_db


async def _amain() -> int:
    cfg = load_config()
    secrets = load_secrets()

    print("db: initializing")
    init_db(config=cfg)

    print("api: pinging")
    base = f"http://{cfg.api.host}:{cfg.api.port}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{base}/health")
            r.raise_for_status()
            print(f"  /health -> {r.json()}")
    except Exception as exc:
        print(f"  api unreachable at {base}: {exc}", file=sys.stderr)

    print("alerts: dispatching test")
    mgr = build_manager(cfg.alerts, secrets)
    if not mgr.channels:
        print("  no channels enabled; skipping")
    else:
        results = await mgr.send("info", "smoke test", "if you see this, alerts are wired up")
        for name, err in results:
            print(f"  {name}: {'OK' if err is None else f'FAIL ({err})'}")

    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
