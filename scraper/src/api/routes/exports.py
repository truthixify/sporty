"""ZIP-download endpoints that wrap `src.export.run_export`.

Each route builds the export tree in a tempdir, zips it, streams the file to
the client, then cleans up via a BackgroundTask. For very large exports
prefer the `scraper export` CLI — these endpoints are sized for "give me one
season" / "give me one schema" use cases.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from src.api.deps import SessionDep
from src.db.models import Schema, Season
from src.export import run_export


router = APIRouter(tags=["Export"])


def _do_export(
    session,
    background_tasks: BackgroundTasks,
    *,
    filename_stem: str,
    **export_kwargs,
) -> FileResponse:
    """Build the export, register the cleanup task, return a FileResponse."""
    tmpdir = Path(tempfile.mkdtemp(prefix="scraper-export-"))
    out_dir = tmpdir / "export"
    try:
        stats = run_export(session, out_dir, bundle=True, **export_kwargs)
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise
    background_tasks.add_task(shutil.rmtree, tmpdir, ignore_errors=True)
    return FileResponse(
        path=stats.output_dir,
        media_type="application/zip",
        filename=f"{filename_stem}.zip",
    )


@router.get(
    "/export",
    summary="Export the whole dataset as a zip",
    description=(
        "Bundles every product's schemas, seasons, matchdays, races into one "
        "zip. Can be large (10s of MB+). Use the `scraper export` CLI for "
        "anything you'd want to scp around."
    ),
)
def export_all(session: SessionDep, background_tasks: BackgroundTasks) -> FileResponse:
    return _do_export(session, background_tasks, filename_stem="scraper-export")


@router.get(
    "/football/schemas/{schema_id}/export",
    summary="Export one football schema (all seasons) as a zip",
    description=(
        "Builds the football tree limited to one schema_id: all seasons, "
        "matchdays, standings, odds. Returns a zip."
    ),
    responses={404: {"description": "No such football schema."}},
)
def export_football_schema(
    session: SessionDep,
    background_tasks: BackgroundTasks,
    schema_id: int,
) -> FileResponse:
    s = session.get(Schema, schema_id)
    if s is None or s.product != "football":
        raise HTTPException(status_code=404, detail="football schema not found")
    return _do_export(
        session, background_tasks,
        filename_stem=f"football-schema-{schema_id}",
        product="football", schema_id=schema_id,
    )


@router.get(
    "/football/seasons/{season_id}/export",
    summary="Export one football season as a zip",
    description="Just one season: matchdays + standings + summary, plus parent schema metadata.",
    responses={404: {"description": "No such season."}},
)
def export_football_season(
    session: SessionDep,
    background_tasks: BackgroundTasks,
    season_id: int,
) -> FileResponse:
    season = session.get(Season, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="season not found")
    return _do_export(
        session, background_tasks,
        filename_stem=f"football-season-{season_id}",
        product="football", schema_id=season.schema_id, season_id=season_id,
    )


def make_race_export_route(*, product: str, label: str) -> Callable:
    """Factory used by app.py to wire one /<product>/schemas/{id}/export
    endpoint per race product, sharing this implementation."""

    def _handler(
        session: SessionDep,
        background_tasks: BackgroundTasks,
        schema_id: int,
    ) -> FileResponse:
        s = session.get(Schema, schema_id)
        if s is None or s.product != product:
            raise HTTPException(status_code=404, detail=f"{label} schema not found")
        return _do_export(
            session, background_tasks,
            filename_stem=f"{product}-schema-{schema_id}",
            product=product, schema_id=schema_id,
        )

    _handler.__name__ = f"export_{product}_schema"
    _handler.__doc__ = (
        f"Export one {label} schema (all races for that schema) as a zip."
    )
    return _handler
