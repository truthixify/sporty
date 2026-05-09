from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


Severity = Literal["info", "warning", "critical"]
ChannelKind = Literal["telegram", "discord", "email", "slack", "console"]
OnConflict = Literal["replace", "ignore"]


class Paths(BaseModel):
    data_dir: Path = Path("./data")
    profile_dir: Path = Path("./browser_profile")
    captures_dir: Path = Path("./data/captures")
    log_dir: Path = Path("./logs")


class Recovery(BaseModel):
    death_401_threshold: int = 5
    death_window_seconds: int = 60
    healthy_after_recovery_seconds: int = 30
    max_recoveries_per_hour: int = 6


class Rotation(BaseModel):
    daily: bool = True


class Capture(BaseModel):
    parent_url: str = "https://www.sportybet.com/ng/virtual"
    enabled_products: list[str] = Field(
        default_factory=lambda: ["football", "dogs", "horses", "speedway", "motorbikes"]
    )
    ignore_resources: list[str] = Field(default_factory=list)
    recovery: Recovery = Field(default_factory=Recovery)
    rotation: Rotation = Field(default_factory=Rotation)
    headless: bool = False


class Database(BaseModel):
    url: str = "sqlite:///./data/scraper.db"


class SeasonDetector(BaseModel):
    require_standings_reset: bool = True


class Parse(BaseModel):
    batch_size: int = 1000
    on_conflict: OnConflict = "replace"
    season_detector: SeasonDetector = Field(default_factory=SeasonDetector)


class Api(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class Thresholds(BaseModel):
    min_frames_per_min: float = 10.0
    min_events_per_hour: float = 30.0
    parser_watermark_stale_seconds: int = 900


class Monitor(BaseModel):
    watchdog_interval_seconds: int = 60
    thresholds: Thresholds = Field(default_factory=Thresholds)


class AlertChannelConfig(BaseModel):
    kind: ChannelKind
    enabled: bool = False
    severity_min: Severity = "warning"


class Alerts(BaseModel):
    default_severity_min: Severity = "warning"
    channels: list[AlertChannelConfig] = Field(default_factory=list)


class Config(BaseModel):
    paths: Paths = Field(default_factory=Paths)
    capture: Capture = Field(default_factory=Capture)
    database: Database = Field(default_factory=Database)
    parse: Parse = Field(default_factory=Parse)
    api: Api = Field(default_factory=Api)
    monitor: Monitor = Field(default_factory=Monitor)
    alerts: Alerts = Field(default_factory=Alerts)


class Secrets(BaseSettings):
    """Sensitive values loaded from .env or the process environment."""

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    discord_webhook_url: str | None = None
    resend_api_key: str | None = None
    email_from: str | None = None
    email_to: str | None = None
    slack_webhook_url: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


DEFAULT_CONFIG_PATH = Path("config.yaml")


def load_config(path: Path | None = None) -> Config:
    """Load YAML config from `path` (or ./config.yaml). Returns defaults if no file is present."""
    yaml_path = path or DEFAULT_CONFIG_PATH
    if not yaml_path.exists():
        return Config()
    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Config(**data)


def load_secrets() -> Secrets:
    """Load secrets from .env (if present) and the process environment."""
    return Secrets()
