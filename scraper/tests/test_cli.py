from typer.testing import CliRunner

from src.cli import app


runner = CliRunner()


def test_root_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for sub in ("capture", "parse", "api", "monitor", "status", "db"):
        assert sub in result.stdout


def test_db_help_lists_init() -> None:
    result = runner.invoke(app, ["db", "--help"])
    assert result.exit_code == 0
    assert "init" in result.stdout


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_monitor_exits_when_no_channels_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["monitor"])
    assert result.exit_code == 2
    assert "no alert channels enabled" in (result.stderr + result.stdout)
