# Scraper Runbook

Operational reference for the Virtustec scraper running on a single VPS. Read
this first when something is on fire. The component-level design lives in
`docs/scraper/spec.md`; this file is the "what do I do right now" version.

## Layout

- Code: `/opt/scraper/scraper/` (project root with `pyproject.toml`)
- DB: `/opt/scraper/scraper/data/scraper.db` (SQLite by default)
- Captures: `/opt/scraper/scraper/data/captures/vs_YYYY-MM-DD.jsonl`
- Browser profile: `/opt/scraper/scraper/browser_profile/`
- Logs: `journalctl -u scraper-*`

Systemd units, all installed in `/etc/systemd/system/`:

| Unit | Purpose |
| --- | --- |
| `scraper-capture.service` | Playwright daemon, writes journal frames |
| `scraper-api.service` | FastAPI health/metrics/queries |
| `scraper-monitor.service` | Watchdog, polls metrics, fires alerts |
| `scraper-parse.timer` | Runs `scraper parse --backfill` every 5 min |
| `scraper-parse.service` | Oneshot parser (invoked by the timer) |

## Bring-up on a fresh VPS

```bash
# as root, on a fresh Ubuntu 22.04
curl -O https://raw.githubusercontent.com/truthixify/sporty/main/scraper/scripts/setup_vps.sh
bash setup_vps.sh
```

After the script finishes, do the manual steps it prints:

1. `cp config.example.yaml config.yaml`, then edit `paths.*`, `database.url`,
   `capture.headless: true`, alert channels. Leave `capture.persistent_profile`
   at its default (`false`) — SportyBet's /virtual page serves the WS feed
   anonymously, so each daemon run uses an ephemeral Chromium profile that's
   discarded on shutdown. No login required.
2. `cp .env.example .env`, then fill in `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
   `RESEND_API_KEY` etc. for the channels you enabled.
3. **No login step needed.** (If SportyBet ever starts gating the page,
   tunnel a VNC session, run `uv run python scripts/bootstrap_login.py` to
   log in once, and set `capture.persistent_profile: true` in config.yaml.)
4. Sanity-check capture by hand for two minutes:
   `sudo -u scraper bash -c 'cd /opt/scraper/scraper && uv run scraper capture --duration 120'`
5. Enable the services:
   ```
   systemctl enable --now scraper-api
   systemctl enable --now scraper-monitor
   systemctl enable --now scraper-capture
   systemctl enable --now scraper-parse.timer
   ```

## Daily checks (automated, but here for reference)

- `curl localhost:8000/metrics/capture` should show non-zero `events_per_hour`
  and a recent `last_event_age_s` (single-digit seconds, hundreds at most).
- `curl localhost:8000/db/stats` should show row counts climbing.
- `journalctl -u scraper-capture --since "1 hour ago"` should be quiet.

## Symptom -> action

| Symptom | Action |
| --- | --- |
| Telegram alert "no frames in 5 min" | `journalctl -u scraper-capture -n 200`. Likely the recovery ladder ran out (Cloudflare or similar). Just `systemctl restart scraper-capture`. (If you've enabled `capture.persistent_profile` and login expired, re-run `bootstrap_login.py` over a VNC tunnel first.) |
| Telegram alert "watermark stuck" | `systemctl status scraper-parse`. If failing, run by hand: `sudo -u scraper bash -c 'cd /opt/scraper/scraper && uv run scraper parse --backfill'` and inspect the traceback. |
| `scraper status` shows lots of 401s | Session expired mid-day. Wait one tick (recovery is automatic) or `systemctl restart scraper-capture`. |
| Watchdog silent for >24h | `systemctl status scraper-monitor`. Check `journalctl -u scraper-monitor`. |
| New product (e.g. virtuals adds basketball) appears in `unknown_blocks` | Add a `Product` subclass under `src/parse/products/`, register it in `src/parse/products/__init__.py`, then re-run `scripts/rebuild_db.py`. |
| Season detection looks wrong (`season_index` jumping every few matchdays) | Either `require_standings_reset: false` is on and a stray bad `matchDay` tripped a boundary, or the capture has a real gap that looks like a reset. Inspect the `seasons` table; manually delete and re-run `detect_seasons` if you need to. |
| DB file too large (> 5 GB) | Cold-archive old events: dump older months to Parquet/CSV and `DELETE` from `events`/`odds`. Or migrate to Postgres (config-only change; see spec section "Postgres path"). |
| Recovery exit code 11 (no iframe) | Page layout probably changed or login was lost. Re-run `bootstrap_login.py`. |
| Recovery exit code 12 (too many recoveries) | The 6/hour budget was burnt. The wrapper sleeps 30 min before restarting. If this fires repeatedly, escalate: something upstream is broken (Cloudflare, account, region). |

## Capture exit codes

Defined in `src/capture/daemon.py`. The systemd wrapper at
`scripts/capture_wrapper.sh` translates them into back-off delays.

| Code | Meaning | Wrapper delay |
| --- | --- | --- |
| 0 | Clean shutdown (SIGTERM, `--duration` reached) | none |
| 1 | Uncaught exception | 60s |
| 10 | Session dead, recovery ladder exhausted | 30s |
| 11 | No virtustec iframe found after 5min | 5min |
| 12 | Too many recoveries this hour | 30min |

## Backfilling from a fresh prototype journal

```bash
sudo -u scraper bash -c 'cd /opt/scraper/scraper && uv run scraper parse --backfill --source /path/to/journals/'
```

To wipe and rebuild the DB from scratch (alembic + parser + season detector +
matchday aggregator):

```bash
sudo -u scraper bash -c 'cd /opt/scraper/scraper && uv run python scripts/rebuild_db.py'
```

## Where to look for help inside the code

- `src/capture/daemon.py` -- exit codes, recovery ladder semantics
- `src/parse/season_detector.py` -- season boundary rule + standings-reset cross-check
- `src/parse/matchday_aggregator.py` -- "all events settled before snapshot" rule
- `src/alerts/manager.py` -- which channel ran on what severity
- `src/api/routes/football.py` -- query endpoints; matchday view is the main consumer surface
