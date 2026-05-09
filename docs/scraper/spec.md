# Virtustec Virtuals Scraper — Engineering Spec

## Purpose

Build a continuously-running, low-touch data collection system that captures every match-level event served by the Virtustec virtual sports platform behind SportyBet's `/virtual` page, parses it into structured records, persists it to a queryable store, and exposes operational health via HTTP + alerting.

This is a **data collection** project. No betting, no automated wagering, no prediction trading. The output is a clean dataset that downstream consumers (analysts, researchers, possibly future modeling experiments) can query. Whether anyone ever finds anything actionable in the data is a separate question handled outside this system.

## Context: existing prototype vs new build

A working **prototype** lives in the sibling directory `vs_scraper/`. It proves the protocol works end-to-end and contains:

- `vs_scraper/main.py` — Playwright capture daemon with daily journal rotation and a 401-recovery ladder.
- `vs_scraper/analyze.py` — full multi-product parser writing per-competition CSVs.
- `vs_scraper/analyze_football.py` — football-only earlier version (superseded).
- `vs_scraper/captures/` — real JSONL captures (~15 MB sample), usable as test fixtures and backfill source.
- `vs_scraper/parsed/` — sample parsed CSVs across products and leagues, useful for sanity-checking new output.

**The new implementation lives in a sibling directory `scraper/`** and is a clean rebuild, not a refactor. The prototype is reference material — read it to understand the protocol and the parsing logic, but do not import from it. The new code starts from a proper package layout, has tests, has config, has CI-friendly structure. Both directories sit at the same level under the repo root.

What the prototype proves and the new build inherits:

- The virtuals UI is a cross-origin iframe at `virtual-games.virtustec.com/mobile-v4`. A single WebSocket at `wss://virtual-proxy.virtustec.com/vs` carries data for all products simultaneously, regardless of which league is on screen.
- The wire protocol is a custom JSON-RPC envelope: each frame has `type: "REQUEST"|"RESPONSE"`, an `xs` correlation id, and either `req` or `res` with HTTP-style fields. Pairing is by `xs`, scoped to a single WS lifetime.
- Relevant resources (basePath `/api/client/v0.1`):
  - `/playlists/` — schema templates: market definitions (~450 odd slots, slot index → bet name), participant rosters with base ratings, countdown duration, content library reference.
  - `/eventBlocks/event/data` — match instances with closing odds, participant snapshot, optional game data.
  - `/eventBlocks/event/result` — settled matches with `finalOutcome` and `wonMarkets`.
  - `/eventBlocks/stats` — league standings + per-team form (football only).
  - `/session/sync` — keepalive.
- Session lifetime is roughly 20 minutes before the `clientId` expires; afterwards every call returns 401. Recovery requires reloading the iframe (cheapest), re-navigating it, or reloading the parent page (heaviest).
- Product detection is via `participants[0].classType`: `FbParticipant`, `DogParticipant`, `HorseParticipant`, `SpeedwayParticipant`, `MotorbikeParticipant`. For schemas, `participantTemplates[0].classType` is more reliable than `gameTypeBase`.
- Football tournaments (`competitionType: "CHAMPION"`, e.g. WorldCup, AfricaCup) are structurally distinct from leagues — multi-stage with `phase: "GROUPS"|"KNOCKOUT"|"FINAL"` — and are stored separately from leagues.

## What the prototype gets wrong

These are the gaps the new build must close. Listed in priority order.

### Critical

1. **Not autonomous.** Manual login required on first run, manual league click to bootstrap the iframe, manual restart when recovery fails. On a headless VPS there is no human to do those things.
2. **Persistent profile portability untested.** The prototype's `browser_profile/` survives across local runs but moving it to a different machine and IP probably triggers Cloudflare/reCAPTCHA Enterprise reauth. Needs a setup procedure that works on a fresh VPS.
3. **No durable storage.** Output is JSONL on disk and CSVs from ad-hoc parsing. No database, no indexes, no idempotent reingest, no watermarks.
4. **No observability.** When capture is silently emitting 401s for an hour, nothing alerts. No metrics endpoint, no log aggregation, no health check.
5. **Single point of failure.** One WS connection, no failover. If the recovery ladder fails the whole pipeline stops.
6. **No season concept.** Football leagues cycle (Premier 2026 = 38 matchdays, then restarts). The prototype treats every match as standalone and dumps standings into a single flat CSV regardless of which season they belong to. This makes the data unusable for anyone who wants to ask "what happened in season 3, matchday 7?"

### Important

7. **Capture is wasteful.** Logs every WS frame including heartbeats and products outside the enabled list. ~50 MB/hour unfiltered; can be cut to ~5 MB/hour with capture-time filtering.
8. **No schema versioning.** Virtustec can change the wire format. Parser silently drops unrecognized fields; nothing alerts on unexpected new ones.
9. **No correctness validation.** Parser produces CSVs but nothing checks `wonMarkets` is consistent with `finalOutcome` and `oddValues`.
10. **Closing-snapshot only.** Odds movement during the betting window (if any) is discarded. Worth capturing in case it carries signal.
11. **Hardcoded everything.** URLs, paths, thresholds all literals in scripts. Belongs in config.
12. **No reproducibility.** A weird parsed value can't always be traced back to the originating WS frame because journal retention isn't enforced and parser doesn't carry source offsets.
13. **No test coverage.** Zero unit tests, zero fixture-based regression tests. Refactoring is risky.

## What "done" looks like

After one-time setup, the system runs unattended on a VPS for at least 30 days without intervention. Specifically:

- Continuous capture with automatic session recovery, including from full session expiry, machine reboot, network blip.
- Captures persisted in a queryable database with proper indices, foreign keys, and idempotent ingestion.
- Football data organized by season and matchday, with standings advancing per matchday, and a season summary derivable from the data.
- A monitoring surface — FastAPI HTTP endpoint + Telegram alerts (with optional additional channels) — that says "we are capturing N events/hour, last seen at T" and notifies a human when the rate falls to zero.
- Reproducible from raw frames: the JSONL is the immutable journal; the database is a derived view that can always be rebuilt from journal.
- Zero secrets in code; one config file with paths, thresholds, and feature flags. Secrets in `.env`.
- Full setup runbook for a fresh VPS, including the first-time login choreography.
- A test suite that runs in CI and covers the parser, the pairing logic, the season detection, and the recovery state machine.

Expected steady-state volume: roughly 200-400 thousand events per month across all enabled products. JSONL ~3-5 GB/month with capture-time filtering; database ~100-300 MB/month. Comfortable on a single small VPS.

## Architecture

Five components, one repo, one process tree on the VPS.

### 1. Capture daemon (`src/scraper/capture/`)

**Responsibility:** open a logged-in browser, attach WS listeners, write raw frames to the journal, monitor health, recover or surrender cleanly.

**Implementation:**

- Playwright with `launch_persistent_context` against a profile directory baked at setup time.
- Listen on every page (including new pages opened by the parent context) for WS events. Filter to virtustec domain at capture time; drop everything else (alive-ng socket.io, Google reCAPTCHA, ads).
- Write each frame as one JSON line: `{ts, dir, kind, data, session_id, capture_version}`. The `session_id` is a UUID generated at daemon startup, included on every line so cross-session pairing in the parser is trivial.
- Rotate journal files daily, named `vs_YYYY-MM-DD.jsonl`. Old files are immutable.
- Health monitor: rolling window of inbound RESPONSE statuses. If 401 rate exceeds threshold over a 60-second window, trigger recovery ladder:
  1. **L1** Reload the iframe in place (`iframe.src = iframe.src`).
  2. **L2** Reload the parent page; rediscover the iframe URL (which carries a fresh `onlineHash`).
  3. **L3** Full parent navigation from scratch.
  Each level gets 30 seconds to prove it worked (clean 200s flowing). If all three fail, exit with a distinct exit code.
- Rate-limit recovery attempts (max 6/hour). Exceeding the limit exits with a different code so the supervisor knows to back off.
- Optional capture-time frame filter: drop frames matching configured ignore patterns (heartbeats, `/tickets/*`, products outside the enabled list). Cuts journal size by ~80%.
- Exit codes:
  - `0` — clean shutdown (SIGTERM)
  - `10` — session dead, recovery ladder exhausted; restart immediately
  - `11` — no virtustec iframe found; probably needs human (relogin or page changed)
  - `12` — too many recoveries this hour; back off
  - `1` — uncaught exception; restart with backoff

The "exit cleanly on unrecoverable failure" pattern is deliberate. A wrapper restarts the process with fresh state. In-process recovery beyond the ladder leads to state-leak bugs that are hard to debug.

### 2. Supervisor (systemd)

**Responsibility:** keep the capture daemon alive across all reasonable failure modes.

`systemd` service unit on Linux with `Restart=always`, `RestartSec=30`, `StartLimitBurst` set high enough to tolerate normal recovery cycles. Different exit codes get different cooldowns via a small wrapper script the unit calls:

| Exit code | Restart delay | Notes |
| --- | --- | --- |
| `0` | none | intentional shutdown |
| `10` | 30s | session dead, normal recovery |
| `11` | 5min + alert | probably needs human |
| `12` | 30min | back off; recovery loop pathological |
| `1`, anything else | 60s | uncaught error |

For local development, a simple shell loop `while true; do uv run scraper capture; sleep 30; done` is fine. The systemd unit is in `systemd/scraper-capture.service`.

### 3. Parser / ingester (`src/scraper/parse/`)

**Responsibility:** convert raw journal frames into structured database rows; detect season boundaries; advance standings per matchday.

Two operating modes, same code:

- **Backfill** (`uv run scraper parse --backfill`): read every JSONL file from a start date, parse, write to database. Idempotent; safe to re-run on the entire corpus. Used after schema changes or initial migration.
- **Incremental** (`uv run scraper parse`): track a watermark per journal file (byte offset, last line number). Each run parses only frames past the watermark. Run via systemd timer every 5 minutes.

Both modes use SQLAlchemy `INSERT ... ON CONFLICT` on natural keys (`e_block_id` for events, `(e_block_id, slot)` for odds, etc.) so re-parsing is safe. The journal is the source of truth; the database is a derived cache.

Product handlers under `src/scraper/parse/products/` follow a common ABC: each implements `extract(block, request_resource) -> list[Record]`. Products: football, dogs, horses, speedway, motorbikes. Adding a new product means adding one file and registering it.

After basic extraction, a second pass runs `season_detector.py`: for each schema in football products, walks events in `event_time` order and assigns `season_id` and `match_day` based on rules in the next section.

### 4. API + monitoring (`src/scraper/api/` + `src/scraper/monitor/`)

**Responsibility:** expose system health, recent data, capture metrics, and structured queries (seasons, matchdays, standings) over HTTP. Watch metrics and trigger alerts.

**FastAPI app** (`src/scraper/api/app.py`), bound to localhost only by default:

```
# Operational
GET  /health                                 # liveness
GET  /ready                                  # readiness; checks DB + recent capture
GET  /metrics                                # JSON metrics
GET  /metrics/capture                        # frames/min, events/hr, last_seen_ts
GET  /metrics/parse                          # rows ingested, watermark per file
GET  /sessions                               # capture session log
GET  /sessions/{session_id}                  # one session detail
GET  /db/stats                               # row counts per table

# Data
GET  /events/recent                          # last N events across products
GET  /events/{e_block_id}                    # one event with all related rows
GET  /events                                 # filterable: product, schema, status, time

# Football structured queries
GET  /football/schemas                       # list leagues with summary
GET  /football/schemas/{schema_id}/seasons   # all seasons of one league
GET  /football/seasons/{season_id}           # season summary: champion, top scorer, etc.
GET  /football/seasons/{season_id}/matchdays # all matchdays in season
GET  /football/seasons/{season_id}/matchdays/{n}   # matchday view: matches + standings
GET  /football/seasons/{season_id}/standings/final # final season standings
GET  /football/standings/at?schema={id}&time={iso} # point-in-time standings
```

A **watchdog** process (`src/scraper/monitor/watchdog.py`) wakes every 60 seconds, hits `/metrics/capture`, compares against thresholds, and fires alerts when:

- frames/min drops below threshold for 5 minutes (capture broken)
- events/hour drops below threshold for 30 minutes (capture or parse broken)
- recovery exit codes (10, 11, 12) appear in capture session log
- watermark hasn't advanced in 15 minutes (parser stuck)

Watchdog runs as its own systemd service.

### 5. Alerting (`src/scraper/alerts/`)

**Responsibility:** deliver notifications via configured channels. **Telegram is primary** with options for additional channels.

Channel pattern:

```python
class AlertChannel(ABC):
    @abstractmethod
    async def send(self, severity: Severity, title: str, body: str) -> None: ...
```

Implementations:

- **`TelegramChannel`** — primary channel, default-on. Uses Bot API. Config: bot token + chat id.
- **`DiscordChannel`** — webhook URL.
- **`EmailChannel`** — Resend HTTP API (`POST /emails` with a Bearer-token API key). No SMTP.
- **`SlackChannel`** — webhook URL.
- **`ConsoleChannel`** — prints to stderr. Default for dev.

A `ChannelManager` reads the config and fans out to all enabled channels. Severity levels: `info`, `warning`, `critical`. Channels can filter by severity (e.g., Telegram for everything, email only for critical).

Adding a new channel = one file implementing the ABC + an entry in `manager.py`. Initial implementations: Telegram and Console (for dev). Discord, Email, Slack are stubs that can be filled in when needed.

## Storage strategy

Two storage layers, each with a clear responsibility. **This is the foundation of the data collection — it must be sensible.**

### Layer 1: JSONL journal (immutable raw)

- Location: `${data_dir}/captures/vs_YYYY-MM-DD.jsonl`
- Lifetime: kept indefinitely (until disk pressure forces rotation to cold storage)
- Format: one JSON object per line, schema: `{ts, dir, kind, data, session_id, capture_version}`
- **Source of truth.** Anything the database can't be rebuilt from journal does not get stored.

The journal is appended to during capture and never rewritten. It is the canonical record. If the database becomes corrupted, deleted, or has a parser bug, the journal regenerates it.

### Layer 2: relational DB (everything else)

**Default: SQLite.** At expected scale (tens of millions of rows after a year), SQLite handles writes from a single ingester and reads from the FastAPI app comfortably. Operational complexity is zero — the database is one file. Backups are `cp`. Migrations are easy.

**Postgres path:** the data layer is built on SQLAlchemy with a database URL in config. Switching to Postgres is a config change + `alembic upgrade head`. Reasons to switch:

- Multiple parallel ingester processes (not currently planned)
- Concurrent writers and heavy concurrent readers
- Cross-machine deployment (database on its own host)
- Row count exceeding comfort zone for SQLite (~100M rows or ~50 GB file)

For phase one, SQLite. The schema is identical either way. **All ORM code must work on both** — no SQLite-specific features, no Postgres-specific features, just SQL the SQLAlchemy core supports across both backends.

### File exports (deferred)

Bulk file exports (Parquet, structured CSV trees) are not part of phase one. The use cases that motivate them — handing off data to someone outside the system, large analytical scans — don't exist yet. The database + the structured API endpoints described above are sufficient for any current consumer.

When file exports become useful, they'll be generated on demand by a `scraper export` command from the database, organized by `(schema_id, season_id, matchday)`. Until then, do not write any export code. The data is in the database; query it from there.

## Seasons, matchdays, and weekly grouping

This is the central organizing concept for football data and the part of the system the prototype does not handle. Race products do not need this — each race is independent.

### The football lifecycle

A football league schema (e.g. Premier 2026, schema_id 41104) has fixed parameters:

- `numParticipants` (e.g. 20)
- `isTwoLegsGroup: true` for leagues — every team plays every other team home and away
- `countdown` between matches (e.g. 180s)

This produces a deterministic season:

- 20 teams × 19 opponents × 2 legs = 380 matches per season
- Matches per matchday = `numParticipants / 2` = 10
- Matchdays per season = 380 / 10 = 38

The Virtustec engine plays through one season, then restarts at matchday 1 with reset standings. We have observed `matchDay` going from 38 back to 1 across a season boundary. This is what we detect.

### Season detection rule

For each `schema_id`, the parser walks events in `event_time` order:

```
state: current_season_id, prev_match_day
for each event in order:
    md = event.match_day
    if prev_match_day is None:
        # first event for this schema; start season 1
        current_season_id = new_season(schema_id, started_at = event.event_time)
    elif md < prev_match_day:
        # matchday went backwards — season boundary
        finalize_season(current_season_id, ended_at = prev_event.event_time)
        current_season_id = new_season(schema_id, started_at = event.event_time)
    event.season_id = current_season_id
    prev_match_day = md
```

A more robust variant also checks for `points` reset to 0 across all teams in the standings at the boundary. If matchday goes 38 → 1 AND standings reset, it's clearly a new season. Use both signals.

### Matchday as the natural unit of grouping

For any football consumer, the matchday is the right unit. A matchday is a small, complete bundle:

- ~10 matches played
- A definitive standings snapshot taken after they finished
- A clear position in the season's progression

The system exposes matchdays as first-class queryable objects. The API endpoint `GET /football/seasons/{season_id}/matchdays/{n}` returns:

```json
{
  "season_id": 42,
  "schema": { "id": 41104, "description": "England 2026 - OS31" },
  "matchday": 17,
  "matches": [
    {
      "e_block_id": 314275,
      "home": "PSG", "away": "Liverpool",
      "home_score": 2, "away_score": 1,
      "odd_match_home": "1.85", "odd_match_draw": "3.40", "odd_match_away": "4.20",
      "won_markets": ["Match_Result_Home", "_2_1", "Over_Under_2_5_over"]
    }
    // ... 9 more
  ],
  "standings": [
    { "rank": 1, "team": "PSG", "points": 38, "wins": 12, "draws": 2, "losses": 3 },
    // ... 19 more
  ],
  "summary": {
    "total_goals": 27,
    "home_wins": 5, "away_wins": 3, "draws": 2,
    "biggest_win": "PSG 4-0 Forest"
  }
}
```

This is the "weekly snapshot" the prototype lacks. It's what an analyst looking at one week of a season actually wants.

### Season summary

A season is summarized when its last matchday completes:

```
GET /football/seasons/{season_id}
```

Returns champion, runner-up, relegation positions (if the schema models them), top scorers (if we capture them), total goals, biggest win of the season, etc.

### Where this lives in the database

A `seasons` table tracks each season instance. A `match_day_snapshots` table holds the bundled matchday view (matches + standings together) as a denormalized cache for fast API reads — populated incrementally by the parser. The denormalization is justified because matchday data is immutable once the matchday's last match settles.

```sql
seasons (
    season_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_id    INTEGER NOT NULL REFERENCES schemas(schema_id),
    season_index INTEGER NOT NULL,           -- 1, 2, 3, ... per schema
    started_at   TEXT NOT NULL,              -- ISO 8601 of first event
    ended_at     TEXT,                       -- NULL if season still in progress
    matchdays_completed INTEGER DEFAULT 0,
    champion_team_id    TEXT,                -- populated when season finalizes
    runner_up_team_id   TEXT,
    raw_summary  JSON,                       -- top scorers, total goals, etc.
    UNIQUE (schema_id, season_index)
);

match_day_snapshots (
    season_id    INTEGER NOT NULL REFERENCES seasons(season_id),
    match_day    INTEGER NOT NULL,
    finalized_ts REAL,                       -- when last match settled
    matches_json    JSON NOT NULL,           -- full match list with odds, scores, won markets
    standings_json  JSON NOT NULL,           -- standings AFTER this matchday
    summary_json    JSON,                    -- aggregates: total_goals, home_wins, etc.
    PRIMARY KEY (season_id, match_day)
);
```

These are derived from the primary tables (`events`, `standings`, `odds`) and can always be rebuilt by re-running the parser. They exist because the alternative — joining and aggregating 5 tables on every API call — is slow and the data never changes once finalized.

### Tournaments are different

Tournaments (`competition_type = "CHAMPION"`) have a finite lifespan: group stage + knockouts + final. They don't repeat the way leagues do — but the same engine may serve "WorldCup Qatar 22" repeatedly with the same schema_id, just generating new outcomes each time.

For tournaments we use the same `seasons` table, but a "season" is one full tournament instance (groups → final). Boundary detection: when `phase` resets from `FINAL` back to `GROUPS`, it's a new tournament instance.

The matchday view for tournaments returns matches grouped by `(phase, match_day)` instead of just `match_day`, since tournaments have parallel structures (e.g., quarter-final leg 1 and leg 2).

## Database schema

SQLAlchemy ORM models, compatible with both SQLite and Postgres.

```sql
-- Schema templates (leagues + tournaments), deduped by id
schemas (
    schema_id          INTEGER PRIMARY KEY,
    product            TEXT NOT NULL,
    kind               TEXT NOT NULL,           -- 'league' | 'tournament'
    description        TEXT,
    description_tag    TEXT,
    competition_type   TEXT,                    -- LEAGUE | CHAMPION | etc.
    competition_subtype TEXT,
    countdown_s        INTEGER,
    market_template_id INTEGER,
    library_id         TEXT,
    content_library    TEXT,
    num_participants   INTEGER,
    is_two_legs_group  BOOLEAN,
    raw                JSON,
    first_seen_ts      REAL NOT NULL,
    last_seen_ts       REAL NOT NULL
);

schema_markets (
    schema_id   INTEGER NOT NULL REFERENCES schemas(schema_id),
    slot        INTEGER NOT NULL,
    market_id   TEXT NOT NULL,
    market_name TEXT NOT NULL,
    odd_id      TEXT NOT NULL,
    odd_name    TEXT NOT NULL,
    PRIMARY KEY (schema_id, slot)
);

schema_participants (
    schema_id   INTEGER NOT NULL REFERENCES schemas(schema_id),
    team_id     TEXT NOT NULL,
    name        TEXT,
    fifa_code   TEXT,
    stars       REAL,
    raw         JSON,
    PRIMARY KEY (schema_id, team_id)
);

-- Season instances, one row per (schema, completed cycle)
seasons (
    season_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_id    INTEGER NOT NULL REFERENCES schemas(schema_id),
    season_index INTEGER NOT NULL,           -- 1, 2, 3, ... within schema
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    matchdays_completed INTEGER DEFAULT 0,
    champion_team_id    TEXT,
    runner_up_team_id   TEXT,
    raw_summary  JSON,
    UNIQUE (schema_id, season_index)
);
CREATE INDEX idx_seasons_schema ON seasons(schema_id);

-- Events: every match or race
events (
    e_block_id     INTEGER PRIMARY KEY,
    schema_id      INTEGER NOT NULL REFERENCES schemas(schema_id),
    season_id      INTEGER REFERENCES seasons(season_id),  -- football: set by season detector; races: NULL
    product        TEXT NOT NULL,
    server_status  TEXT,
    event_time     TEXT,
    captured_ts    REAL NOT NULL,
    settled_ts     REAL,

    -- Football-specific
    home_team_id   TEXT,
    away_team_id   TEXT,
    home_score     INTEGER,
    away_score     INTEGER,
    phase          TEXT,                        -- GROUPS | KNOCKOUT | FINAL (tournaments)
    match_day      INTEGER,
    leg_order      INTEGER,
    week_day       INTEGER,
    champ_id       INTEGER,

    -- Race-specific
    num_runners    INTEGER,
    winner_id      TEXT,
    second_id      TEXT,
    third_id       TEXT,
    final_order    TEXT,
    track_condition REAL,
    weather        TEXT,
    surface        TEXT,
    distance       REAL,

    -- Shared
    won_markets    TEXT,
    won_market_count INTEGER,
    content_duration REAL,
    media_id       TEXT,

    -- Forensics
    raw_data       JSON,
    raw_result     JSON
);
CREATE INDEX idx_events_schema_time   ON events(schema_id, event_time);
CREATE INDEX idx_events_season_md     ON events(season_id, match_day);
CREATE INDEX idx_events_product       ON events(product);
CREATE INDEX idx_events_status        ON events(server_status);
CREATE INDEX idx_events_settled_ts    ON events(settled_ts);

-- Closing-snapshot odds, one row per slot
odds (
    e_block_id INTEGER NOT NULL REFERENCES events(e_block_id),
    slot       INTEGER NOT NULL,
    odds       REAL NOT NULL,
    PRIMARY KEY (e_block_id, slot)
);

-- Time-series odds (only populated if movement is observed)
odds_history (
    e_block_id  INTEGER NOT NULL REFERENCES events(e_block_id),
    slot        INTEGER NOT NULL,
    snapshot_ts REAL NOT NULL,
    odds        REAL NOT NULL,
    PRIMARY KEY (e_block_id, slot, snapshot_ts)
);

-- Football: who's playing on each side
event_participants_football (
    e_block_id INTEGER NOT NULL REFERENCES events(e_block_id),
    side       TEXT NOT NULL,                  -- 'home' | 'away'
    team_id    TEXT NOT NULL,
    stars      REAL,
    PRIMARY KEY (e_block_id, side)
);

-- Race: per-runner attributes
event_runners (
    e_block_id INTEGER NOT NULL REFERENCES events(e_block_id),
    runner_id  TEXT NOT NULL,
    trap       INTEGER,
    name       TEXT,
    prob       REAL,
    form       REAL,
    star       INTEGER,
    ability    REAL,
    speed      REAL,
    stamina    REAL,
    wins       REAL,
    place      REAL,
    pace       TEXT,
    forecast   TEXT,
    raw        JSON,
    PRIMARY KEY (e_block_id, runner_id)
);

-- Football: standings snapshot per match (carries season + matchday)
standings (
    e_block_id    INTEGER NOT NULL REFERENCES events(e_block_id),
    season_id     INTEGER REFERENCES seasons(season_id),
    match_day     INTEGER,                     -- redundant with event but indexed for queries
    team_id       TEXT NOT NULL,
    ranking       INTEGER,
    points        INTEGER,
    wins          INTEGER,
    draws         INTEGER,
    losses        INTEGER,
    goals_for     INTEGER,
    goals_against INTEGER,
    goal_diff     INTEGER,
    history       TEXT,
    PRIMARY KEY (e_block_id, team_id)
);
CREATE INDEX idx_standings_season_md ON standings(season_id, match_day);

-- Denormalized per-matchday view (matches + standings + summary, ready for API)
match_day_snapshots (
    season_id    INTEGER NOT NULL REFERENCES seasons(season_id),
    match_day    INTEGER NOT NULL,
    finalized_ts REAL,                         -- when last match of this matchday settled
    matches_json    JSON NOT NULL,
    standings_json  JSON NOT NULL,
    summary_json    JSON,
    PRIMARY KEY (season_id, match_day)
);

-- Operational
parse_watermarks (
    journal_file  TEXT PRIMARY KEY,
    last_offset   INTEGER NOT NULL,
    last_line     INTEGER NOT NULL,
    parsed_count  INTEGER NOT NULL,
    last_run_ts   REAL NOT NULL
);

capture_sessions (
    session_id   TEXT PRIMARY KEY,
    started_ts   REAL NOT NULL,
    ended_ts     REAL,
    end_reason   TEXT,
    frames_in    INTEGER DEFAULT 0,
    frames_out   INTEGER DEFAULT 0,
    error_count  INTEGER DEFAULT 0,
    notes        TEXT
);

metrics_snapshots (
    snapshot_ts     REAL PRIMARY KEY,
    frames_per_min  REAL,
    events_per_hour REAL,
    error_rate      REAL,
    raw             JSON
);

alerts_log (
    alert_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    fired_ts     REAL NOT NULL,
    severity     TEXT NOT NULL,
    title        TEXT NOT NULL,
    body         TEXT,
    channels     TEXT,
    delivered    INTEGER DEFAULT 0
);
```

## Repo layout

This describes the **`scraper/`** directory, which sits as a sibling to the existing `vs_scraper/`.

```
scraper/
  README.md
  SPEC.md
  runbook.md
  pyproject.toml
  uv.lock
  .python-version
  config.example.yaml
  .env.example
  .gitignore                         # browser_profile/, *.db, data/, .env, logs/

  src/
    scraper/
      __init__.py
      __main__.py
      cli.py                         # `scraper {capture, parse, api, monitor, status, db}`
      config.py                      # pydantic-settings config loader

      capture/
        __init__.py
        daemon.py
        browser.py
        health.py
        recovery.py
        filters.py
        journal.py

      parse/
        __init__.py
        ingest.py                    # incremental + backfill
        pairing.py                   # xs correlation, scoped per session
        schemas.py                   # /playlists/ → schema rows
        season_detector.py           # NEW: assigns season_id + match_day
        matchday_aggregator.py       # NEW: builds match_day_snapshots
        products/
          __init__.py
          base.py
          football.py
          races.py
          dogs.py
          horses.py
          speedway.py
          motorbikes.py

      db/
        __init__.py
        engine.py
        models.py
        migrations/
        queries.py                   # high-level queries (matchday view, season summary)

      api/
        __init__.py
        app.py
        deps.py
        routes/
          __init__.py
          health.py
          metrics.py
          events.py
          sessions.py
          db.py
          football.py                # NEW: seasons, matchdays, standings

      alerts/
        __init__.py
        base.py
        manager.py
        telegram.py
        discord.py
        email.py
        slack.py
        console.py

      monitor/
        __init__.py
        watchdog.py
        thresholds.py

  systemd/
    scraper-capture.service
    scraper-api.service
    scraper-monitor.service
    scraper-parse.service
    scraper-parse.timer

  scripts/
    setup_vps.sh
    bootstrap_login.py
    rebuild_db.py
    smoke_test.py

  tests/
    fixtures/
      sample_capture.jsonl
      sample_schema_premier.json
      sample_event_data.json
      sample_event_result.json
      sample_two_seasons.jsonl       # NEW: capture spanning a season boundary
    conftest.py
    test_pairing.py
    test_parser_football.py
    test_parser_races.py
    test_season_detector.py          # NEW
    test_matchday_aggregator.py      # NEW
    test_capture_health.py
    test_recovery_ladder.py
    test_alerts.py
    test_db_models.py
    test_api_routes.py

  docs/
    protocol.md
    database.md                      # ER diagram + season/matchday query examples
```

## Configuration

`config.yaml` (committed as `config.example.yaml`):

```yaml
paths:
  data_dir: ./data
  profile_dir: ./browser_profile
  captures_dir: ./data/captures
  log_dir: ./logs

capture:
  parent_url: "https://www.sportybet.com/ng/virtual"
  enabled_products: [football, dogs, horses, speedway, motorbikes]
  ignore_resources: [/tickets/findByTime]
  recovery:
    death_401_threshold: 5
    death_window_seconds: 60
    healthy_after_recovery_seconds: 30
    max_recoveries_per_hour: 6
  rotation:
    daily: true
  headless: false                    # set true on VPS once stable

database:
  url: "sqlite:///./data/scraper.db"
  # url: "postgresql+psycopg://user:pass@host/db"

parse:
  batch_size: 1000
  on_conflict: replace
  season_detector:
    require_standings_reset: true    # don't trip on a single weird matchday value

api:
  host: "127.0.0.1"
  port: 8000

monitor:
  watchdog_interval_seconds: 60
  thresholds:
    min_frames_per_min: 10
    min_events_per_hour: 30
    parser_watermark_stale_seconds: 900

alerts:
  default_severity_min: warning
  channels:
    - kind: telegram
      enabled: true
      severity_min: info
    - kind: discord
      enabled: false
      severity_min: warning
    - kind: email
      enabled: false
      severity_min: critical
    - kind: slack
      enabled: false
      severity_min: warning
    - kind: console
      enabled: true
      severity_min: info
```

`.env.example`:

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

DISCORD_WEBHOOK_URL=

RESEND_API_KEY=
EMAIL_FROM=
EMAIL_TO=

SLACK_WEBHOOK_URL=
```

Loaded via `pydantic-settings`. Anything in `.env` overrides the YAML for sensitive fields.

## Setup runbook

### Developer machine (one-time)

1. `git clone <repo> && cd scraper`
2. `cp config.example.yaml config.yaml && cp .env.example .env`
3. `uv sync`
4. `uv run python -m playwright install chromium`
5. `uv run python scripts/bootstrap_login.py` — opens a headed browser, prompts for manual login, exits cleanly.
6. `uv run scraper capture --duration 60` — sanity check.
7. `uv run scraper parse --backfill` — sanity check; populates the DB.
8. `uv run scraper status` — sanity check.

### VPS (one-time)

Assumes fresh Ubuntu 22.04 LTS with sudo. Recommended region: Frankfurt or London (research item — see Open Problems).

1. `sudo apt update && sudo apt install -y python3-pip python3-venv git tmux fail2ban ufw`
2. `curl -LsSf https://astral.sh/uv/install.sh | sh`
3. `git clone <repo> /opt/scraper && cd /opt/scraper`
4. `uv sync`
5. `uv run python -m playwright install chromium`
6. `uv run python -m playwright install-deps`
7. **Transfer logged-in profile from dev machine:** `scp -r dev:browser_profile vps:/opt/scraper/browser_profile` (or use `bootstrap_login.py` over a VNC tunnel: `ssh -L 5900:localhost:5900 vps`, run X server, complete login).
8. Verify session survives: `uv run scraper capture --duration 120`
9. `cp config.example.yaml config.yaml && cp .env.example .env` — edit for VPS paths and secrets (Telegram bot token, etc.).
10. Initialize DB: `uv run scraper db init`
11. Install systemd units:
    ```
    sudo cp systemd/*.service systemd/*.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now scraper-capture
    sudo systemctl enable --now scraper-api
    sudo systemctl enable --now scraper-monitor
    sudo systemctl enable --now scraper-parse.timer
    ```
12. Verify:
    - `journalctl -u scraper-capture -f` — frames flowing
    - `curl localhost:8000/metrics/capture` — non-zero rates
    - `uv run scraper status` — green

### Ongoing operations

| Symptom | Action |
| --- | --- |
| Telegram alert "no frames in 5 min" | `journalctl -u scraper-capture -n 200`. Likely session died, recovery exhausted. Tunnel VNC, re-login, restart capture service. |
| Telegram alert "watermark stuck" | Parser broken. `systemctl status scraper-parse`. |
| `scraper status` shows lots of 401s | Session expired mid-day. Wait for next recovery (~5 min) or `systemctl restart scraper-capture`. |
| Monitoring silent for >24h | Watchdog itself broken. `systemctl status scraper-monitor`. |
| New product appears in `unknown/` | Virtustec added a sport. Add a product file under `parse/products/` and run backfill. |
| Season detection seems off (e.g. season_index keeps incrementing every few matchdays) | Either `require_standings_reset` is false and a stray bad matchDay tripped it, or the capture has a gap that looks like a reset. Inspect `seasons` table; manually merge if needed. |
| DB file growing too large | Archive old events to a cold table; SQLite happiest under 10 GB. |

## Open problems requiring research

These are not blockers but should be answered empirically during or shortly after build.

### Session lifetime

Why does the session die at ~20 minutes? Possibilities: server-side TTL, inactivity timeout, cumulative bot-detection signal, region/IP fingerprint mismatch.

**Test:** instrument the daemon to log session lifetime distribution across many sessions. If tight at ~20 min → server TTL → fix is auto-rerefresh before expiry.

### Iframe URL durability

The `onlineHash` rotates. How often, and is the URL still valid hours later?

**Test:** capture iframe URL on each restart for a week. Try reusing yesterday's URL.

### VPS region choice

Cloudflare may behave differently across regions.

**Test:** try the same login flow from droplets in 2-3 regions.

### Time-series odds movement

Are odds static during the betting window or do they move?

**Test:** capture every `/event/data` for the same `eBlockId` within its betting window. Log differences.

### Race `prob` validation

Race products publish `prob` per runner. `sum(prob)` should approach 1 minus the operator margin.

**Test:** load a few thousand race events, compute `sum(prob)` per race.

### Season boundary edge cases

The detection rule `match_day < prev_match_day` is straightforward but relies on continuous capture. If we miss frames around a boundary (matchDay 38 → ... → matchDay 5), the rule still works but we lose accuracy on `started_at`.

**Test:** simulate gaps in the test fixture and verify season boundaries are detected within a tolerable window.

### Multi-account redundancy

Single account = single point of failure. Two accounts on different IPs would dedupe to a more reliable stream. Defer until single-account uptime hurts the dataset.

## Migration from the prototype

The prototype's parsed CSVs are not migrated. The new build re-parses from the prototype's JSONL files:

1. After new build is deployed and DB is initialized:
2. `cp ../vs_scraper/captures/*.jsonl scraper/data/captures/`
3. `uv run scraper parse --backfill`
4. Verify: total event count vs prototype's CSV summaries should match within rounding. Season count per league should be small (probably 1-2 with the prototype's limited capture).

## Implementation order (suggested for Claude Code)

A pragmatic build order. Don't try to write everything at once.

1. **Project skeleton** — `pyproject.toml`, `uv sync`, package layout, empty modules, working `cli.py` with stub commands. Acceptance: `uv run scraper --help`.
2. **Config + DB models** — `config.py`, SQLAlchemy models, alembic migrations, `scraper db init`. Acceptance: `sqlite3 data/scraper.db .schema` shows all tables including `seasons` and `match_day_snapshots`.
3. **Parser, with fixtures** — port `analyze.py` logic into `parse/`. Use a sample JSONL from `vs_scraper/captures/` as fixture. Cover football and one race product first. Acceptance: `uv run scraper parse --backfill --source ../vs_scraper/captures/` populates the DB; tests pass.
4. **Season detector + matchday aggregator** — second-pass logic that assigns `season_id` to events, builds `match_day_snapshots`, finalizes seasons. Tests use a synthetic fixture spanning a 38→1 boundary. Acceptance: querying `seasons` shows 1 row per detected season; `match_day_snapshots` has the bundled JSON view.
5. **Capture daemon** — port `main.py` with the recovery ladder, journal writes via `journal.py`, session id generation, exit codes. Acceptance: `uv run scraper capture --duration 60` produces a journal that the parser can ingest.
6. **API** — FastAPI app with `/metrics/capture`, `/events/recent`, and the `/football/seasons/.../matchdays/{n}` endpoint reading from `match_day_snapshots`. Acceptance: matchday endpoint returns the bundled JSON with matches and standings.
7. **Watchdog + alerts** — Telegram channel implementing the ABC, console channel for dev. Acceptance: alert fires when capture is killed.
8. **systemd units + setup script** — production deployment artifacts. Acceptance: VPS runbook works on a fresh droplet.
9. **Other channels (Discord, Email, Slack)** — fill in the stubs.

The prototype is the reference. Many decisions in `vs_scraper/main.py` and `vs_scraper/analyze.py` already reflect lessons learned the hard way; copy the logic, just not the structure.

## Honest framing

This system collects data. It does not bet, predict, recommend, or optimize. The downstream uses of the data — analysis, research, statistical tests, possibly modeling experiments — are someone else's problem and outside this spec.

Two things worth keeping in mind for whoever does eventually look at the data:

- Virtustec/Golden Race outcomes come from RNGs certified by independent labs (iTech Labs, GLI, BMM Testlabs depending on jurisdiction). Certification specifically tests that outputs pass standard randomness batteries. If the certification is honest, no model trained on outcome history will beat the operator's margin (typically 6-15% on virtuals) over a meaningful sample.
- The data is still genuinely interesting for non-prediction questions: empirically validating randomness claims, studying simulator design, understanding how operator margins decompose across markets, measuring how `stars` ratings translate into observed outcomes across seasons, and so on. Those are the real research questions this dataset enables.

## Appendix A — protocol reference

```
WS URL:        wss://virtual-proxy.virtustec.com/vs
Frame format:  JSON, both directions
Envelope:
  REQUEST  { "type": "REQUEST",  "xs": <int>, "ts": <ms>,
             "req": { method, query, resource, headers } }
  RESPONSE { "type": "RESPONSE", "xs": <int>, "ts": <ms>,
             "res": { statusCode, body, ... } }
Pairing:       match REQUEST.xs to RESPONSE.xs within a single WS lifetime.

Resources (basePath /api/client/v0.1):
  GET /session/sync             keepalive; returns SessionSettings
  GET /session/loginHwId        hardware-fingerprint login
  GET /session/loginOnlineHash  hash-based login
  GET /playlists/               schema templates (heavy)
  GET /eventBlocks/event/data   match instances + closing odds
  GET /eventBlocks/event/result settled matches (finalOutcome, wonMarkets)
  GET /eventBlocks/stats        league standings + form (football only)
  GET /tickets/findByTime       bet history; usually 401 from this proxy

Status codes:
  200  Success
  401  Unauthorized; >5 in 60s window indicates session death
  701  Server-side game/business error; ignorable individually
```

## Appendix B — classType reference

Participant classTypes (event blocks):

- `FbParticipant`, `FootballParticipant` — football
- `DogParticipant` — greyhound racing
- `HorseParticipant` — horse racing
- `SpeedwayParticipant` — speedway
- `MotorbikeParticipant`, `MotorbikesParticipant` — motorbike racing

Block-level classTypes (when participants absent):

- `FbEventBlockStats`, `FootballEventBlockStats` — football stats
- `RaceEventBlockStats` — generic race stats
- `FbEventBlockData`, `FootballEventBlockData` — football match-day metadata
- `RaceEventBlockData` — generic race metadata

Schema templates use `participantTemplates[0].classType` as the authoritative product signal.

## Appendix C — numbers worth knowing

- Football league countdown: 180s. ~480 events/day per league. ~14,500/month.
- Premier 2026 (`isTwoLegsGroup: true`, 20 teams): 380 matches per season, 38 matchdays, ~32 hours of real time per season. So ~22 seasons per month if uninterrupted.
- Race countdowns: typically 60-180s.
- All enabled products at steady state: ~200-400 thousand events/month.
- JSONL volume unfiltered: ~50 MB/hour, ~36 GB/month.
- JSONL volume with filtering: ~5 MB/hour, ~3.6 GB/month.
- SQLite database size after one month: 100-300 MB.
- A single $5/month VPS handles all of this comfortably.
- ~450 odd slots per football match. Numbered slots map to bet types via `schema_markets`.

## Appendix D — files in `vs_scraper/` (reference only)

| Path | Purpose |
| --- | --- |
| `vs_scraper/main.py` | Reference capture daemon. |
| `vs_scraper/analyze.py` | Reference multi-product parser. Logic ports to `src/scraper/parse/`, output goes to DB. |
| `vs_scraper/analyze_football.py` | Earlier football-only parser. Superseded. |
| `vs_scraper/captures/*.jsonl` | Real captured frames; usable as test fixtures and backfill source. |
| `vs_scraper/parsed/` | Output of the prototype parser. Useful for sanity-checking new parser output. |
| `vs_scraper/browser_profile/` | Logged-in profile. Migrate to new build's `browser_profile/` for first VPS deploy. |