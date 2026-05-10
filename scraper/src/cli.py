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

    engine = make_engine(cfg)
    Session = make_session_factory(engine)
    with Session() as session:
        stats = ingest_journals(files, session, incremental=not backfill)
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
    import asyncio

    from src.alerts import build_manager
    from src.config import load_config, load_secrets
    from src.db import make_engine, make_session_factory
    from src.monitor import run_watchdog

    cfg = load_config()
    secrets = load_secrets()
    engine = make_engine(cfg)
    session_factory = make_session_factory(engine)
    manager = build_manager(cfg.alerts, secrets, session_factory=session_factory)
    if not manager.channels:
        typer.echo("monitor: no alert channels enabled in config", err=True)
        raise typer.Exit(code=2)
    asyncio.run(run_watchdog(cfg, manager, session_factory=session_factory))


@app.command()
def status() -> None:
    """Print a one-shot summary of capture, parser, and DB health."""
    import httpx

    from src.config import load_config

    cfg = load_config()
    base = f"http://{cfg.api.host}:{cfg.api.port}"
    try:
        with httpx.Client(timeout=5.0) as client:
            metrics = client.get(f"{base}/metrics/capture").json()
            stats = client.get(f"{base}/db/stats").json()
    except Exception as exc:
        typer.echo(f"status: api unreachable at {base}: {exc}", err=True)
        raise typer.Exit(code=2)
    typer.echo(
        f"capture: events_per_hour={metrics.get('events_per_hour')} "
        f"frames_per_min={metrics.get('frames_per_min')} "
        f"last_event_age_s={metrics.get('last_event_age_s')}"
    )
    counts = stats.get("row_counts", {})
    typer.echo(
        f"db: events={counts.get('events')} schemas={counts.get('schemas')} "
        f"seasons={counts.get('seasons')} matchday_snapshots={counts.get('match_day_snapshots')} "
        f"odds={counts.get('odds')} standings={counts.get('standings')}"
    )


@app.command()
def alert_test(
    severity: str = typer.Option("info", "--severity", help="info, warning, or critical."),
) -> None:
    """Fire one test alert through every enabled channel.

    Use this to verify your Telegram/Discord/email/etc. plumbing actually
    works without having to wait for a real threshold breach.
    """
    import asyncio

    from src.alerts import build_manager
    from src.config import load_config, load_secrets
    from src.db import make_engine, make_session_factory

    cfg = load_config()
    secrets = load_secrets()
    engine = make_engine(cfg)
    session_factory = make_session_factory(engine)
    manager = build_manager(cfg.alerts, secrets, session_factory=session_factory)
    if not manager.channels:
        typer.echo("alert-test: no channels enabled in config", err=True)
        raise typer.Exit(code=2)

    async def _go() -> None:
        results = await manager.send(
            severity,  # type: ignore[arg-type]
            "scraper: alert plumbing test",
            "if you can see this, your alert channels are wired up correctly.",
        )
        for name, err in results:
            mark = "OK" if err is None else f"FAIL ({err})"
            typer.echo(f"  {name}: {mark}")

    asyncio.run(_go())


@app.command()
def diagnose() -> None:
    """Inspect every part of the local stack and explain what's wrong.

    Use this when you see a low-frame-rate or stale-data alert and want to
    know which piece of the pipeline is broken without poking at SQLite or
    the journal by hand.
    """
    import json
    import subprocess
    import time
    from collections import Counter

    from src.config import load_config

    cfg = load_config()

    def head(text: str) -> None:
        typer.echo(f"\n=== {text} ===")

    head("config")
    typer.echo(f"data_dir:     {cfg.paths.data_dir}")
    typer.echo(f"captures_dir: {cfg.paths.captures_dir}")
    typer.echo(f"profile_dir:  {cfg.paths.profile_dir}")
    typer.echo(f"database:     {cfg.database.url}")

    head("chromium processes")
    try:
        result = subprocess.run(
            ["pgrep", "-fl", "Chrome for Testing"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                typer.echo(f"  {line}")
        else:
            typer.echo("  none running")
    except Exception as exc:
        typer.echo(f"  could not check: {exc}")

    head("journal files")
    captures = sorted(cfg.paths.captures_dir.glob("vs_*.jsonl")) if cfg.paths.captures_dir.exists() else []
    if not captures:
        typer.echo(f"  no journal files in {cfg.paths.captures_dir}")
        typer.echo("  → capture daemon hasn't written anything; check it's running")
    for jf in captures[-3:]:
        st = jf.stat()
        age = time.time() - st.st_mtime
        typer.echo(f"  {jf.name}  size={st.st_size:>10}  modified {int(age)}s ago")

    head("recent journal contents")
    if captures:
        latest = captures[-1]
        kind_counts: Counter = Counter()
        resource_counts: Counter = Counter()
        try:
            with latest.open("rb") as f:
                f.seek(max(0, latest.stat().st_size - 200_000))
                tail = f.read().decode("utf-8", errors="replace")
            for line in tail.splitlines():
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                kind = rec.get("kind") or rec.get("dir")
                kind_counts[kind] += 1
                data = rec.get("data")
                if isinstance(data, str):
                    try:
                        d = json.loads(data)
                        req = d.get("req") or {}
                        if req.get("resource"):
                            resource_counts[req["resource"]] += 1
                    except Exception:
                        pass
        except Exception as exc:
            typer.echo(f"  could not read tail: {exc}")
        typer.echo(f"  tail kinds: {dict(kind_counts)}")
        for res, n in resource_counts.most_common(8):
            typer.echo(f"    {n:>5}  {res}")
        if not any("/eventBlocks/event/data" in r for r in resource_counts):
            typer.echo(
                "  → no /eventBlocks/event/data in recent journal. "
                "Iframe is connected but not on the virtuals page. "
                "Re-run bootstrap_login.py and click into a sport."
            )

    head("database")
    try:
        from sqlalchemy import func, select

        from src.db import make_engine, make_session_factory
        from src.db.models import (
            CaptureSession, Event, ParseWatermark, Schema,
        )

        engine = make_engine(cfg)
        Session = make_session_factory(engine)
        with Session() as s:
            ev_count = s.scalar(select(func.count()).select_from(Event)) or 0
            sc_count = s.scalar(select(func.count()).select_from(Schema)) or 0
            last_ts = s.scalar(select(func.max(Event.captured_ts)))
            typer.echo(f"  events:  {ev_count}")
            typer.echo(f"  schemas: {sc_count}")
            if last_ts is not None:
                age = time.time() - float(last_ts)
                typer.echo(f"  freshest event captured {int(age)}s ago")
            else:
                typer.echo("  no events yet")
            head("parser watermarks")
            wms = s.scalars(select(ParseWatermark).order_by(ParseWatermark.last_run_ts.desc())).all()
            if not wms:
                typer.echo("  no watermarks; parser hasn't run yet")
            for wm in wms[:5]:
                age = time.time() - wm.last_run_ts
                typer.echo(
                    f"  {wm.journal_file}  offset={wm.last_offset:>10}  "
                    f"last_run {int(age)}s ago  parsed={wm.parsed_count}"
                )
            head("recent capture sessions")
            sess = s.scalars(
                select(CaptureSession).order_by(CaptureSession.started_ts.desc()).limit(5)
            ).all()
            if not sess:
                typer.echo("  no capture sessions recorded; daemon may not have shut down cleanly yet")
            for x in sess:
                start_age = time.time() - x.started_ts
                end = (
                    f"ended {int(time.time() - x.ended_ts)}s ago: {x.end_reason}"
                    if x.ended_ts else "still running"
                )
                typer.echo(
                    f"  {x.session_id[:8]}...  started {int(start_age)}s ago  "
                    f"frames_in={x.frames_in} {end}"
                )
    except Exception as exc:
        typer.echo(f"  could not query db: {exc}")


@db_app.command("init")
def db_init() -> None:
    """Create the database file and apply all migrations."""
    from src.db import init_db

    init_db()
    typer.echo("db: ready")


@db_app.command("prune-watermarks")
def db_prune_watermarks(
    missing: bool = typer.Option(
        True, "--missing/--no-missing",
        help="Drop watermarks whose journal file no longer exists. Default on.",
    ),
    duplicates: bool = typer.Option(
        True, "--duplicates/--no-duplicates",
        help="Drop watermarks that are aliases of another (different string, "
             "same resolved path). Keeps the absolute-path version. Default on.",
    ),
    external: bool = typer.Option(
        False, "--external",
        help="Drop watermarks whose journal file is outside the configured "
             "captures_dir. Useful when you backfilled from a one-off source.",
    ),
    file: Optional[str] = typer.Option(
        None, "--file",
        help="Drop the watermark for exactly this `journal_file` value "
             "(string match against the row's key, not a resolved path).",
    ),
    all_: bool = typer.Option(
        False, "--all",
        help="Wipe every watermark and force the next parse to re-scan from "
             "offset 0 across the board. Overrides every other filter.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Delete stale parse_watermarks rows.

    Default action removes:
    - **Missing**: rows pointing at journal files that no longer exist
      (e.g., the prototype's journal you backfilled from once).
    - **Duplicates**: multiple rows for the same physical file (e.g.,
      `data/captures/x.jsonl` and `/abs/path/data/captures/x.jsonl` from
      before/after the absolute-path migration). Keeps the absolute one.

    Opt-in modes:
    - **--external**: rows for files outside the configured captures_dir.
    - **--file PATH**: drop exactly the row whose journal_file equals PATH.
    - **--all**: wipe everything.
    """
    from collections import defaultdict
    from pathlib import Path

    from sqlalchemy import select

    from src.config import load_config
    from src.db import make_engine, make_session_factory
    from src.db.models import ParseWatermark

    cfg = load_config()
    try:
        captures_root = cfg.paths.captures_dir.resolve()
    except (OSError, RuntimeError):
        captures_root = cfg.paths.captures_dir

    engine = make_engine(cfg)
    Session = make_session_factory(engine)
    with Session() as session:
        rows = session.scalars(select(ParseWatermark)).all()
        doomed: list[ParseWatermark] = []

        if all_:
            doomed = list(rows)
        else:
            if missing:
                doomed.extend(r for r in rows if not Path(r.journal_file).exists())
            if duplicates:
                # Group by resolved absolute path; if a group has more than
                # one row, keep the one whose journal_file IS the absolute
                # path (or fall back to the most-recently-run row).
                by_resolved: dict[str, list[ParseWatermark]] = defaultdict(list)
                for r in rows:
                    try:
                        resolved = str(Path(r.journal_file).resolve())
                    except (OSError, RuntimeError):
                        resolved = r.journal_file
                    by_resolved[resolved].append(r)
                for resolved, group in by_resolved.items():
                    if len(group) <= 1:
                        continue
                    keep = next((r for r in group if r.journal_file == resolved), None)
                    if keep is None:
                        keep = max(group, key=lambda r: r.last_run_ts)
                    for r in group:
                        if r is not keep:
                            doomed.append(r)
            if external:
                for r in rows:
                    try:
                        resolved = Path(r.journal_file).resolve()
                    except (OSError, RuntimeError):
                        # Can't resolve; treat as external since it can't be
                        # under our captures_dir either
                        doomed.append(r)
                        continue
                    try:
                        resolved.relative_to(captures_root)
                    except ValueError:
                        doomed.append(r)
            if file is not None:
                for r in rows:
                    if r.journal_file == file:
                        doomed.append(r)

        # Dedupe doomed (a row might match both --missing and --duplicates)
        seen_ids = set()
        unique_doomed: list[ParseWatermark] = []
        for r in doomed:
            rid = id(r)
            if rid not in seen_ids:
                seen_ids.add(rid)
                unique_doomed.append(r)
        doomed = unique_doomed

        if not doomed:
            typer.echo("nothing to prune.")
            return

        typer.echo(f"about to delete {len(doomed)} watermark(s):")
        for r in doomed:
            typer.echo(f"  - {r.journal_file}  (last_offset={r.last_offset})")

        if not yes:
            confirm = typer.prompt("proceed? [y/N]", default="n")
            if confirm.strip().lower() not in ("y", "yes"):
                typer.echo("aborted.")
                raise typer.Exit(code=1)

        for r in doomed:
            session.delete(r)
        session.commit()
        typer.echo(f"deleted {len(doomed)} watermark(s).")


@app.command()
def dev(
    no_capture: bool = typer.Option(False, "--no-capture", help="Skip the capture daemon."),
    no_parse: bool = typer.Option(False, "--no-parse", help="Skip the parse loop."),
    no_api: bool = typer.Option(False, "--no-api", help="Skip the FastAPI server."),
    no_monitor: bool = typer.Option(False, "--no-monitor", help="Skip the watchdog."),
    parse_interval: int = typer.Option(60, "--parse-interval", help="Seconds between parse runs."),
    headless: Optional[bool] = typer.Option(
        None, "--headless/--headed", help="Override the configured Chromium headless mode."
    ),
) -> None:
    """Run capture + parse loop + api + monitor in one terminal with multiplexed output."""
    from src.dev import run_dev_stack

    code = run_dev_stack(
        capture=not no_capture,
        parse=not no_parse,
        api=not no_api,
        monitor=not no_monitor,
        parse_interval_s=parse_interval,
        headless=headless,
    )
    raise typer.Exit(code=code)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
