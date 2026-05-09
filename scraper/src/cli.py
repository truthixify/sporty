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
    headless: Optional[bool] = typer.Option(
        None, "--headless/--headed", help="Override the configured headless mode."
    ),
) -> None:
    """Run the WebSocket capture daemon."""
    import asyncio

    from src.capture import run_capture
    from src.config import load_config

    cfg = load_config()
    code = asyncio.run(run_capture(cfg, duration_s=duration, headless=headless))
    raise typer.Exit(code=code)


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
    skip_postprocess: bool = typer.Option(
        False, "--skip-postprocess", help="Skip season detection and matchday aggregation."
    ),
) -> None:
    """Parse JSONL journal frames into the database."""
    from src.config import load_config
    from src.db import init_db, make_engine, make_session_factory
    from src.parse import aggregate_matchdays, detect_seasons, ingest_journals

    cfg = load_config()
    init_db(config=cfg)

    src_dir = source or cfg.paths.captures_dir
    if not src_dir.exists():
        typer.echo(f"parse: source not found: {src_dir}", err=True)
        raise typer.Exit(code=2)

    files = sorted(p for p in src_dir.glob("*.jsonl") if p.is_file())
    if not files:
        typer.echo(f"parse: no .jsonl files in {src_dir}", err=True)
        raise typer.Exit(code=2)

    if not backfill:
        typer.echo("parse: incremental mode not implemented; pass --backfill", err=True)
        raise typer.Exit(code=1)

    engine = make_engine(cfg)
    Session = make_session_factory(engine)
    with Session() as session:
        stats = ingest_journals(files, session)
        seasons_added = matchdays_added = 0
        if not skip_postprocess:
            seasons_added = detect_seasons(
                session,
                require_standings_reset=cfg.parse.season_detector.require_standings_reset,
            )
            matchdays_added = aggregate_matchdays(session)
        session.commit()

    typer.echo(
        f"parse: files={stats.files} frames={stats.frames} pairs={stats.pairs} "
        f"schemas={stats.schemas} events={stats.events} odds={stats.odds} "
        f"standings={stats.standings} runners={stats.runners} "
        f"unknown={stats.unknown_blocks} "
        f"seasons_new={seasons_added} matchday_snapshots={matchdays_added}"
    )


@app.command()
def api(
    host: Optional[str] = typer.Option(None, "--host"),
    port: Optional[int] = typer.Option(None, "--port"),
    reload: bool = typer.Option(False, "--reload", help="Reload on code changes (dev only)."),
) -> None:
    """Run the FastAPI app exposing health, metrics, and structured queries."""
    import uvicorn

    from src.config import load_config

    cfg = load_config()
    bind_host = host or cfg.api.host
    bind_port = port or cfg.api.port
    uvicorn.run("src.api.app:create_app", host=bind_host, port=bind_port, factory=True, reload=reload)


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
