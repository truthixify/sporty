from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.db.models import (
    Event,
    EventParticipantFootball,
    EventRunner,
    Odds,
    Schema,
    SchemaMarket,
    SchemaParticipant,
    Standing,
)


# A product handler emits any combination of these row types. Empty lists are
# fine for products that don't have a particular concept (races have no
# standings; football has no runners).
ExtractedRows = tuple[
    list[Event],
    list[Odds],
    list[EventParticipantFootball],
    list[EventRunner],
    list[Standing],
]


class Product(ABC):
    """Per-product handler that knows how to detect events of its kind in the
    wire data and how to map them onto the database row types."""

    name: str
    """Short identifier used in events.product (e.g. 'football')."""

    schema_class_types: frozenset[str]
    """`participantTemplates[0].classType` values that mark a schema as ours."""

    event_participant_class_types: frozenset[str]
    """`participants[0].classType` values that mark an event block as ours."""

    stats_class_types: frozenset[str] = frozenset()
    """`stats.classType` values for stats blocks of this product (football only)."""

    data_class_types: frozenset[str] = frozenset()
    """`data.classType` values for blocks with no participants (e.g. metadata)."""

    @classmethod
    def matches_schema(cls, tpl: dict[str, Any]) -> bool:
        templates = tpl.get("participantTemplates") or []
        if templates and isinstance(templates[0], dict):
            ct = templates[0].get("classType")
            if ct in cls.schema_class_types:
                return True
        return False

    @classmethod
    def matches_event_block(cls, block: dict[str, Any]) -> bool:
        for ev in block.get("events") or []:
            data = (ev or {}).get("data") or {}
            parts = data.get("participants") or []
            if parts and isinstance(parts[0], dict):
                ct = parts[0].get("classType")
                if ct in cls.event_participant_class_types:
                    return True
        stats = block.get("stats") or {}
        if isinstance(stats, dict) and stats.get("classType") in cls.stats_class_types:
            return True
        data = block.get("data") or {}
        if isinstance(data, dict) and data.get("classType") in cls.data_class_types:
            return True
        return False

    @abstractmethod
    def extract_event_rows(
        self,
        block: dict[str, Any],
        resource: str,
        captured_ts: float,
    ) -> ExtractedRows:
        """Return rows derived from one /event/data or /event/result block."""

    def extract_stats_rows(
        self,
        block: dict[str, Any],
        captured_ts: float,
    ) -> list[Standing]:
        """Return standings rows from a /eventBlocks/stats block. Default: none."""
        return []


def schema_rows_from_template(
    tpl: dict[str, Any],
    product: str,
    captured_ts: float,
) -> tuple[Schema, list[SchemaMarket], list[SchemaParticipant]]:
    """Map one `/playlists/` template into Schema + child rows. Pure function."""
    schema_id = _to_int(tpl.get("id"))
    f = tpl.get("filter") or {}
    schedulers = tpl.get("schedulerConfiguration") or []
    sched = schedulers[0] if schedulers else {}
    daily = (sched.get("dailySchedule") or [{}])[0] if sched else {}

    competition_type = f.get("competitionType")
    kind = "tournament" if competition_type == "CHAMPION" else "league"

    schema = Schema(
        schema_id=schema_id,
        product=product,
        kind=kind,
        description=tpl.get("description"),
        description_tag=tpl.get("descriptionTag"),
        competition_type=competition_type,
        competition_subtype=f.get("competitionSubType"),
        countdown_s=_to_int(daily.get("countdown")),
        market_template_id=_to_int(tpl.get("marketTemplateId")),
        library_id=f.get("libraryId"),
        content_library=f.get("contentLibrary"),
        num_participants=_to_int(f.get("numParticipants")),
        is_two_legs_group=_to_bool(f.get("isTwoLegsGroup")),
        raw=tpl,
        first_seen_ts=captured_ts,
        last_seen_ts=captured_ts,
    )

    markets: list[SchemaMarket] = []
    seen_slots: set[int] = set()
    for m in tpl.get("marketTemplates") or []:
        for o in (m or {}).get("odds") or []:
            slot = _to_int(o.get("value"))
            if slot is None or slot in seen_slots:
                continue
            seen_slots.add(slot)
            markets.append(SchemaMarket(
                schema_id=schema_id,
                slot=slot,
                market_id=str(m.get("id", "")),
                market_name=str(m.get("name", "")),
                odd_id=str(o.get("id", "")),
                odd_name=str(o.get("name", "")),
            ))

    participants: list[SchemaParticipant] = []
    seen_teams: set[str] = set()
    for p in tpl.get("participantTemplates") or []:
        team_id = str(p.get("id", ""))
        if not team_id or team_id in seen_teams:
            continue
        seen_teams.add(team_id)
        participants.append(SchemaParticipant(
            schema_id=schema_id,
            team_id=team_id,
            name=p.get("name"),
            fifa_code=p.get("fifaCode"),
            stars=_to_float(p.get("stars")),
            raw=p,
        ))

    return schema, markets, participants


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_bool(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)
