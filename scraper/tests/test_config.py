from __future__ import annotations

from pathlib import Path

import yaml

from src.config import Config, Secrets, load_config


def test_default_config_when_file_missing(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "missing.yaml")
    assert isinstance(cfg, Config)
    assert cfg.api.host == "127.0.0.1"
    assert cfg.api.port == 8000
    assert cfg.parse.batch_size == 1000
    assert "football" in cfg.capture.enabled_products


def test_loads_example_yaml() -> None:
    cfg = load_config(Path("config.example.yaml"))
    assert cfg.capture.parent_url.endswith("/virtual")
    assert cfg.capture.recovery.death_401_threshold == 5
    assert cfg.database.url.startswith("sqlite:///")
    kinds = [c.kind for c in cfg.alerts.channels]
    assert kinds == ["telegram", "discord", "email", "slack", "console"]


def test_yaml_overrides_defaults(tmp_path: Path) -> None:
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(yaml.safe_dump({"api": {"port": 9999}}))
    cfg = load_config(yaml_path)
    assert cfg.api.port == 9999
    assert cfg.api.host == "127.0.0.1"


def test_secrets_defaults_to_none(monkeypatch) -> None:
    for var in (
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL",
        "SMTP_HOST", "SMTP_USER", "SMTP_PASS", "EMAIL_FROM", "EMAIL_TO",
        "SLACK_WEBHOOK_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    secrets = Secrets(_env_file=None)
    assert secrets.telegram_bot_token is None
    assert secrets.smtp_port == 587
