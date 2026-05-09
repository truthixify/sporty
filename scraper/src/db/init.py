from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from src.config import Config, load_config


DEFAULT_ALEMBIC_INI = Path("alembic.ini")


def init_db(
    config: Config | None = None,
    alembic_ini: Path = DEFAULT_ALEMBIC_INI,
) -> None:
    """Apply all alembic migrations to the configured database.

    Creates the data directory if it doesn't exist. Idempotent: re-running on
    an up-to-date database is a no-op.
    """
    cfg = config or load_config()
    cfg.paths.data_dir.mkdir(parents=True, exist_ok=True)

    alembic_cfg = AlembicConfig(str(alembic_ini))
    alembic_cfg.set_main_option("sqlalchemy.url", cfg.database.url)
    command.upgrade(alembic_cfg, "head")
