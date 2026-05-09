# Database Reference

ER diagram and example queries for the scraper SQLite/Postgres database. The
SQLAlchemy models live in `src/db/models.py`; alembic migrations in
`src/db/migrations/`. High-level read queries are in `src/db/queries.py`.

## ER diagram

```
                schemas
                schema_id (PK)              <----------------- describes ----- /playlists/
                product, kind, description
                num_participants, ...
                  |
                  | 1:N                                       1:N
                  +---> schema_markets (slot -> market_name + odd_name)
                  |
                  +---> schema_participants (team_id -> name, fifa_code, stars)
                  |
                  +---> seasons (season_id PK, season_index, started_at, ended_at)
                  |       |
                  |       | 1:N
                  |       +---> events (e_block_id PK)
                  |              schema_id, season_id (nullable, set by detector)
                  |              product, server_status, event_time, captured_ts, settled_ts
                  |              home_team_id, away_team_id, home_score, away_score   <- football
                  |              phase, match_day, leg_order, week_day                <- football
                  |              num_runners, winner_id, second_id, third_id          <- races
                  |              final_order, surface, distance, weather              <- races
                  |              won_markets, won_market_count, content_duration
                  |              raw_data, raw_result (JSON)
                  |                |
                  |                | 1:N
                  |                +---> odds (slot -> closing odds)
                  |                +---> odds_history (slot, snapshot_ts -> price-when-it-moved)
                  |                +---> event_participants_football (side -> team_id, stars)
                  |                +---> event_runners (runner_id -> trap, name, prob, ...)
                  |                +---> standings (team_id -> ranking, points, ...)
                  |
                  +---> match_day_snapshots (denormalized)
                          (season_id, phase, match_day) PK
                          matches_json, standings_json, summary_json

  Operational tables (no FKs):
    parse_watermarks (journal_file PK -> last_offset, last_line, parsed_count, last_run_ts)
    capture_sessions (session_id PK -> started_ts, ended_ts, end_reason, frames_in/out, error_count)
    metrics_snapshots (snapshot_ts PK -> frames_per_min, events_per_hour, raw)
    alerts_log (alert_id PK -> fired_ts, severity, title, body, channels, delivered)
```

## Tables, in dependency order

| Table                          | Notes                                                                              |
| ------------------------------ | ---------------------------------------------------------------------------------- |
| `schemas`                      | One row per upstream `playlistId`; `kind` is `league` / `tournament` / `unknown`   |
| `schema_markets`               | (schema_id, slot) -> market_name + odd_name. The slot is what odds[slot] refers to |
| `schema_participants`          | (schema_id, team_id) -> roster snapshot                                            |
| `seasons`                      | (schema_id, season_index). Detected by `season_detector`. Tournaments included     |
| `events`                       | One row per `eBlockId`. Football-specific and race-specific columns coexist        |
| `odds`                         | (e_block_id, slot) -> closing-snapshot price                                       |
| `odds_history`                 | (e_block_id, slot, snapshot_ts) -> populated only when price moved                 |
| `event_participants_football`  | (e_block_id, side) where side is `home` / `away`                                   |
| `event_runners`                | (e_block_id, runner_id) -> trap, name, race-specific stats                         |
| `standings`                    | (e_block_id, team_id) -> ranking, points, ... at that match's snapshot             |
| `match_day_snapshots`          | (season_id, phase, match_day) -> bundled JSON view: matches + standings + summary  |
| `parse_watermarks`             | journal_file -> resume position for incremental parser                             |
| `capture_sessions`             | session_id -> daemon-run audit row                                                 |
| `metrics_snapshots`            | snapshot_ts -> watchdog tick history                                               |
| `alerts_log`                   | alert_id -> alert delivery audit                                                   |

## Why JSON columns

Two reasons:

1. **Forensics on `events`**: `raw_data` and `raw_result` carry the source
   sub-blocks verbatim. If the parser drops a field we later realize matters,
   the raw JSON is still there to backfill from.
2. **Denormalized snapshots on `match_day_snapshots`**: `matches_json` and
   `standings_json` package what the API needs in one row, so the matchday
   endpoint is a single-row read instead of a 5-table join. The data is
   immutable once a matchday's last match settles, so no consistency issue.

SQLAlchemy `JSON` works on both SQLite and Postgres. On SQLite it's
TEXT-encoded; on Postgres it becomes `json` (not `jsonb`, since we don't
query into the contents).

## Common queries

### Last 50 events across all products

```sql
SELECT e_block_id, product, schema_id, event_time, home_score, away_score, winner_id
FROM events
ORDER BY captured_ts DESC
LIMIT 50;
```

API: `GET /events/recent`

### One event with everything

Done programmatically (see `src/db/queries.py:event_detail`); roughly:

```sql
SELECT * FROM events WHERE e_block_id = ?;
SELECT * FROM event_participants_football WHERE e_block_id = ?;
SELECT * FROM event_runners WHERE e_block_id = ?;
SELECT * FROM odds WHERE e_block_id = ? ORDER BY slot;
SELECT * FROM standings WHERE e_block_id = ? ORDER BY ranking;
```

API: `GET /events/{e_block_id}`

### Football leagues with counts

```sql
SELECT s.schema_id, s.description, s.num_participants,
       (SELECT COUNT(*) FROM seasons WHERE schema_id = s.schema_id) AS seasons,
       (SELECT COUNT(*) FROM events WHERE schema_id = s.schema_id) AS events
FROM schemas s
WHERE s.product = 'football'
ORDER BY s.schema_id;
```

API: `GET /football/schemas`

### Matchday view (pre-aggregated)

```sql
SELECT matches_json, standings_json, summary_json, finalized_ts
FROM match_day_snapshots
WHERE season_id = ? AND phase = ? AND match_day = ?;
```

API: `GET /football/seasons/{season_id}/matchdays/{n}?phase=GROUPS`

For leagues, `phase` defaults to the empty string. For tournaments, the
caller passes one of `GROUPS` / `KNOCKOUT` / `FINAL`.

### Standings at a point in time

Find the latest event before the cutoff, then read its standings snapshot:

```sql
WITH latest AS (
  SELECT e_block_id
  FROM events
  WHERE schema_id = ? AND event_time IS NOT NULL AND event_time <= ?
  ORDER BY event_time DESC
  LIMIT 1
)
SELECT s.team_id, s.ranking, s.points, s.wins, s.draws, s.losses,
       s.goals_for, s.goals_against
FROM standings s
JOIN latest l ON l.e_block_id = s.e_block_id
ORDER BY s.ranking;
```

API: `GET /football/standings/at?schema=41104&time=2026-05-09T15:00:00Z`

### Odds movement for one event/slot

```sql
SELECT snapshot_ts, odds
FROM odds_history
WHERE e_block_id = ? AND slot = ?
ORDER BY snapshot_ts;
```

Only populated when the price actually moved within an ingest run; for
events whose odds never moved, `odds_history` has just the first reading
(the value also lives in `odds`).

### Who's been alerted recently

```sql
SELECT fired_ts, severity, title, channels, delivered
FROM alerts_log
WHERE fired_ts >= ?
ORDER BY fired_ts DESC;
```

### Capture session history

```sql
SELECT session_id, started_ts, ended_ts, end_reason,
       frames_in, frames_out, error_count
FROM capture_sessions
ORDER BY started_ts DESC
LIMIT 20;
```

API: `GET /sessions`. End reasons that page someone via the watchdog:
`recovery_exhausted` (exit 10), `no_iframe` (exit 11),
`too_many_recoveries` (exit 12).

## Migrations

| Revision         | Description                                                            |
| ---------------- | ---------------------------------------------------------------------- |
| `c8f123e664c0`   | initial schema (all tables created from `Base.metadata`)               |
| `ce21561175fd`   | add `phase` to `match_day_snapshots` PK so tournaments can disambiguate |

Apply with `uv run scraper db init` (idempotent). For Postgres deployments
the same migrations work via SQLAlchemy core; switch the `database.url` in
config and run `db init`.

## Postgres path

Config-only switch from SQLite:

```yaml
database:
  url: "postgresql+psycopg://user:pass@host:5432/scraper"
```

Then `uv run scraper db init` creates the tables and applies migrations.
Reasons to switch (per spec):

- Multiple parallel ingester processes
- Concurrent writers and heavy concurrent readers
- Cross-machine deployment (database on its own host)
- Row count exceeding comfort zone for SQLite (~100M rows or ~50 GB)
