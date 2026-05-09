from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Event, Schema, Season, Standing


# Tournament phase ordering. A tournament instance walks GROUPS -> KNOCKOUT
# -> FINAL; when phase reverses (current_rank < prev_rank) we treat that as a
# new tournament instance.
_PHASE_RANK = {
    "GROUPS": 0,
    "KNOCKOUT": 1,
    "FINAL": 2,
}


def detect_seasons(session: Session, *, require_standings_reset: bool = True) -> int:
    """Walk events for each football schema, group them into seasons, and tag
    each event's `season_id`. Two flavors:

    - **Leagues** (`schema.kind == 'league'`): boundary when match_day runs
      backwards (38 -> 1). Optionally cross-checked by requiring all team
      points to be zero in the standings of the new first event.
    - **Tournaments** (`schema.kind == 'tournament'`): boundary when phase
      runs backwards (FINAL -> GROUPS), which is how the engine signals a
      fresh tournament instance.

    Returns the count of new `Season` rows created.
    """
    schemas = session.scalars(
        select(Schema).where(Schema.product == "football").order_by(Schema.schema_id)
    ).all()
    new_seasons = 0
    for schema in schemas:
        if schema.kind == "tournament":
            new_seasons += _detect_tournament(session, schema)
        else:
            new_seasons += _detect_league(session, schema, require_standings_reset)
    session.flush()
    return new_seasons


def _detect_league(
    session: Session,
    schema: Schema,
    require_standings_reset: bool,
) -> int:
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
        return 0

    existing_by_index = _existing_seasons_by_index(session, schema.schema_id)
    current: Season | None = None
    prev_md: int | None = None
    prev_event: Event | None = None
    new_seasons = 0

    for event in events:
        md = event.match_day
        start_new = False
        if prev_md is None:
            start_new = True
        elif md is not None and md < prev_md:
            if not require_standings_reset or _all_points_zero(session, event.e_block_id):
                start_new = True

        if start_new:
            if current is not None and prev_event is not None:
                current.ended_at = prev_event.event_time
                current.matchdays_completed = max(
                    current.matchdays_completed or 0, prev_md or 0
                )
            current, created = _get_or_create_season(
                session, schema.schema_id, existing_by_index, event.event_time,
                next_index=(current.season_index + 1) if current is not None else 1,
            )
            new_seasons += int(created)

        assert current is not None
        _assign_season_to_event(session, event, current)
        prev_md = md
        prev_event = event

    if current is not None and prev_md is not None:
        current.matchdays_completed = max(current.matchdays_completed or 0, prev_md)
    return new_seasons


def _detect_tournament(session: Session, schema: Schema) -> int:
    events = session.scalars(
        select(Event)
        .where(
            Event.schema_id == schema.schema_id,
            Event.event_time.is_not(None),
        )
        .order_by(Event.event_time, Event.e_block_id)
    ).all()
    if not events:
        return 0

    existing_by_index = _existing_seasons_by_index(session, schema.schema_id)
    current: Season | None = None
    prev_phase_rank: int | None = None
    prev_event: Event | None = None
    new_seasons = 0

    for event in events:
        rank = _PHASE_RANK.get((event.phase or "").upper())
        if rank is None:
            # Skip events with unrecognized phase rather than tripping
            # spurious boundaries on bad data.
            continue

        start_new = False
        if prev_phase_rank is None:
            start_new = True
        elif rank < prev_phase_rank:
            start_new = True

        if start_new:
            if current is not None and prev_event is not None:
                current.ended_at = prev_event.event_time
            current, created = _get_or_create_season(
                session, schema.schema_id, existing_by_index, event.event_time,
                next_index=(current.season_index + 1) if current is not None else 1,
            )
            new_seasons += int(created)

        assert current is not None
        _assign_season_to_event(session, event, current)
        prev_phase_rank = rank
        prev_event = event

    return new_seasons


def _existing_seasons_by_index(session: Session, schema_id: int) -> dict[int, Season]:
    rows = session.scalars(
        select(Season).where(Season.schema_id == schema_id).order_by(Season.season_index)
    ).all()
    return {s.season_index: s for s in rows}


def _get_or_create_season(
    session: Session,
    schema_id: int,
    existing_by_index: dict[int, Season],
    started_at: str | None,
    *,
    next_index: int,
) -> tuple[Season, bool]:
    season = existing_by_index.get(next_index)
    if season is not None:
        if not season.started_at and started_at:
            season.started_at = started_at
        return season, False
    season = Season(schema_id=schema_id, season_index=next_index, started_at=started_at or "")
    session.add(season)
    session.flush()
    existing_by_index[next_index] = season
    return season, True


def _assign_season_to_event(session: Session, event: Event, season: Season) -> None:
    event.season_id = season.season_id
    for s in session.scalars(
        select(Standing).where(Standing.e_block_id == event.e_block_id)
    ):
        s.season_id = season.season_id


def _all_points_zero(session: Session, e_block_id: int) -> bool:
    standings = session.scalars(
        select(Standing).where(Standing.e_block_id == e_block_id)
    ).all()
    if not standings:
        return False
    return all((s.points or 0) == 0 for s in standings)
