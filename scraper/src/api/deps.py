from __future__ import annotations

from typing import Annotated, Iterator

from fastapi import Depends
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import Config, load_config
from src.db.engine import make_engine, make_session_factory


_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None
_config: Config | None = None


def configure(config: Config) -> None:
    """Bind a Config to module state. Called by the app factory at startup so
    every request handler shares one engine."""
    global _engine, _factory, _config
    _config = config
    _engine = make_engine(config)
    _factory = make_session_factory(_engine)


def get_config() -> Config:
    if _config is None:
        configure(load_config())
    assert _config is not None
    return _config


def get_session() -> Iterator[Session]:
    if _factory is None:
        configure(get_config())
    assert _factory is not None
    session = _factory()
    try:
        yield session
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session)]
ConfigDep = Annotated[Config, Depends(get_config)]
