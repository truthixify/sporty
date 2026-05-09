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


def test_capture_stub_exits_nonzero() -> None:
    result = runner.invoke(app, ["capture"])
    assert result.exit_code == 1
    assert "not yet implemented" in result.stderr or "not yet implemented" in result.stdout
