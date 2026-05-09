from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import Config


def make_engine(config: Config, *, echo: bool = False) -> Engine:
    """Build a SQLAlchemy engine from the configured database URL."""
    return create_engine(config.database.url, echo=echo, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory bound to `engine`."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
