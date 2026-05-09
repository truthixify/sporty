from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from src import __version__

app = typer.Typer(
    name="scraper",
    help="Virtustec virtual sports data collection scraper.",
    no_args_is_help=True,
    add_completion=False,
)
db_app = typer.Typer(
    name="db",
    help="Database operations.",
    no_args_is_help=True,
)
app.add_typer(db_app, name="db")


def _not_implemented(name: str) -> None:
    typer.echo(f"{name}: not yet implemented", err=True)
    raise typer.Exit(code=1)


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", "-V", help="Print version and exit.", is_eager=True
    ),
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.command()
def capture(
    duration: int = typer.Option(
        0, "--duration", help="Run for N seconds then exit (0 = run forever)."
    ),
    headless: bool = typer.Option(
        False, "--headless/--headed", help="Run the browser headless."
    ),
) -> None:
    """Run the WebSocket capture daemon."""
    _not_implemented("capture")


@app.command()
def parse(
    backfill: bool = typer.Option(
        False, "--backfill", help="Re-parse every journal file from scratch."
    ),
    source: Optional[Path] = typer.Option(
        None,
        "--source",
        help="Directory of JSONL captures to parse (defaults to configured captures_dir).",
    ),
) -> None:
    """Parse JSONL journal frames into the database."""
    _not_implemented("parse")


@app.command()
def api(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """Run the FastAPI app exposing health, metrics, and structured queries."""
    _not_implemented("api")


@app.command()
def monitor() -> None:
    """Run the watchdog that polls metrics and fires alerts."""
    _not_implemented("monitor")


@app.command()
def status() -> None:
    """Print a one-shot summary of capture, parser, and DB health."""
    _not_implemented("status")


@db_app.command("init")
def db_init() -> None:
    """Create the database file and apply all migrations."""
    from src.db import init_db

    init_db()
    typer.echo("db: ready")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
