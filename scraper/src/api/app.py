from __future__ import annotations

from fastapi import FastAPI

from src.api import deps
from src.api.routes import db, events, exports, football, health, metrics, races, sessions
from src.config import Config, load_config


_API_DESCRIPTION = """
Read-only API over the Virtustec virtual sports dataset assembled by the
scraper. The dataset is built from WebSocket frames captured off SportyBet's
`/virtual` page; events are continuously ingested into a SQLite (or Postgres)
store and exposed here.

**Products** in the dataset:

- `football` - leagues and tournaments. Has `home_*`/`away_*` columns,
  `match_day`, `phase`, `won_markets`, plus standings.
- `dogs`, `horses`, `speedway`, `motorbikes`, `mma` - race-style products.
  Has `num_runners`, `winner_id`, `second_id`, `third_id`, `final_order`,
  per-runner detail, and surface/distance/weather metadata.

The events table holds both shapes in one row, so when you ask the events
endpoints for a race you'll see `home_score`/`match_day` as null (those are
football-only). Prefer the product-specific fields per row's `product`.

**Server status** on each event explains why scores might be null:

- `SCHEDULED` - we've seen the event announcement but no result yet.
- `STARTED` - the match is in flight.
- `FINISHED` - settled; scores and won_markets populated.

Routes are grouped by concern: see the tag panels below.
""".strip()


_OPENAPI_TAGS = [
    {
        "name": "Health",
        "description": (
            "Liveness, readiness, and rolling capture/parse metrics. The "
            "watchdog hits these to decide when to fire alerts."
        ),
    },
    {
        "name": "Sessions",
        "description": (
            "Capture-daemon session history. Each row records when a daemon "
            "started, why it stopped, and how many frames it ingested."
        ),
    },
    {
        "name": "Database",
        "description": "Row counts and other DB-level introspection.",
    },
    {
        "name": "Events",
        "description": (
            "Cross-product event queries. Lists are intentionally compact; "
            "use `/events/{e_block_id}` for the full payload (odds, runners, "
            "standings, raw forensics)."
        ),
    },
    {
        "name": "Football",
        "description": (
            "Football-specific structured queries: schemas, seasons, the "
            "denormalized matchday view, point-in-time standings."
        ),
    },
    {
        "name": "Dogs",
        "description": (
            "Greyhound racing. List schemas (race series), inspect events, "
            "trap-position bias, top winning dogs."
        ),
    },
    {
        "name": "Horses",
        "description": (
            "Horse racing. Same shape as the other race products — schemas, "
            "events, post-position bias, top winning horses."
        ),
    },
    {
        "name": "Speedway",
        "description": (
            "Speedway. Schemas, events, grid-position bias, top winners."
        ),
    },
    {
        "name": "Motorbikes",
        "description": (
            "Motorbike racing. Schemas, events, grid-position bias, top "
            "winning riders."
        ),
    },
    {
        "name": "MMA",
        "description": (
            "Mixed martial arts. Two-fighter events with a winner_id. "
            "`winners-by-trap` here is just `corner 1 vs corner 2`."
        ),
    },
    {
        "name": "Export",
        "description": (
            "Bundle the dataset into downloadable zips. The whole-DB endpoint "
            "is sized for occasional snapshots; per-schema and per-season "
            "endpoints are the everyday way to grab a chunk for offline use."
        ),
    },
]


def create_app(config: Config | None = None) -> FastAPI:
    cfg = config or load_config()
    deps.configure(cfg)

    app = FastAPI(
        title="Virtustec Scraper API",
        version="0.0.1",
        description=_API_DESCRIPTION,
        openapi_tags=_OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(sessions.router)
    app.include_router(db.router)
    app.include_router(events.router)
    app.include_router(football.router)
    app.include_router(races.make_race_router(product="dogs", tag="Dogs", label="dog racing"))
    app.include_router(races.make_race_router(product="horses", tag="Horses", label="horse racing"))
    app.include_router(races.make_race_router(product="speedway", tag="Speedway", label="speedway"))
    app.include_router(races.make_race_router(product="motorbikes", tag="Motorbikes", label="motorbike racing"))
    app.include_router(races.make_race_router(product="mma", tag="MMA", label="MMA"))
    app.include_router(exports.router)
    # Per-race export endpoints, sharing one handler factory.
    for prod, label in [
        ("dogs", "dog racing"),
        ("horses", "horse racing"),
        ("speedway", "speedway"),
        ("motorbikes", "motorbike racing"),
        ("mma", "MMA"),
    ]:
        app.add_api_route(
            f"/{prod}/schemas/{{schema_id}}/export",
            exports.make_race_export_route(product=prod, label=label),
            methods=["GET"],
            tags=["Export"],
            summary=f"Export one {label} schema as a zip",
        )
    return app
