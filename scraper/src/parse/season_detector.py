from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Event, Schema, Season, Standing


def detect_seasons(session: Session, *, require_standings_reset: bool = True) -> int:
    """Walk events for each football schema in `event_time` order and assign
    `season_id`. Creates `Season` rows on detected boundaries (matchday running
    backwards, optionally also requiring a points-reset across all standings).

    Returns the number of new Season rows created.
    """
    schemas = session.scalars(
        select(Schema).where(Schema.product == "football").order_by(Schema.schema_id)
    ).all()
    new_seasons = 0

    for schema in schemas:
        events = session.scalars(
            select(Event)
            .where(
                Event.schema_id == schema.schema_id,
                Event.match_day.is_not(None),
                Event.event_time.is_not(None),
            )
            .order_by(Event.event_time, Event.e_block_id)
        ).all()
        if not events:
            continue

        existing = session.scalars(
            select(Season)
            .where(Season.schema_id == schema.schema_id)
            .order_by(Season.season_index)
        ).all()
        existing_by_index = {s.season_index: s for s in existing}

        current: Season | None = None
        prev_md: int | None = None
        prev_event: Event | None = None

        for event in events:
            md = event.match_day
            assert md is not None

            start_new = False
            if prev_md is None:
                start_new = True
            elif md < prev_md:
                if not require_standings_reset or _all_points_zero(session, event.e_block_id):
                    start_new = True

            if start_new:
                if current is not None and prev_event is not None:
                    current.ended_at = prev_event.event_time
                    current.matchdays_completed = max(
                        current.matchdays_completed or 0, prev_md or 0
                    )
                next_index = (current.season_index + 1) if current is not None else 1
                season = existing_by_index.get(next_index)
                if season is None:
                    season = Season(
                        schema_id=schema.schema_id,
                        season_index=next_index,
                        started_at=event.event_time,
                    )
                    session.add(season)
                    session.flush()
                    existing_by_index[next_index] = season
                    new_seasons += 1
                else:
                    if not season.started_at:
                        season.started_at = event.event_time
                current = season

            assert current is not None
            event.season_id = current.season_id

            for s in session.scalars(
                select(Standing).where(Standing.e_block_id == event.e_block_id)
            ):
                s.season_id = current.season_id

            prev_md = md
            prev_event = event

        if current is not None and prev_md is not None:
            current.matchdays_completed = max(current.matchdays_completed or 0, prev_md)

    session.flush()
    return new_seasons


def _all_points_zero(session: Session, e_block_id: int) -> bool:
    standings = session.scalars(
        select(Standing).where(Standing.e_block_id == e_block_id)
    ).all()
    if not standings:
        return False
    return all((s.points or 0) == 0 for s in standings)
