from __future__ import annotations

from fastapi import FastAPI

from src.api import deps
from src.api.routes import db, events, football, health, metrics, sessions
from src.config import Config, load_config


def create_app(config: Config | None = None) -> FastAPI:
    cfg = config or load_config()
    deps.configure(cfg)

    app = FastAPI(
        title="Virtustec Scraper API",
        version="0.0.1",
        docs_url="/docs",
        redoc_url=None,
    )
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(events.router)
    app.include_router(sessions.router)
    app.include_router(db.router)
    app.include_router(football.router)
    return app
