from __future__ import annotations

from typing import Any

from src.db.models import Schema, SchemaMarket, SchemaParticipant
from src.parse.products.base import _to_bool, _to_float, _to_int


def schema_rows_from_template(
    tpl: dict[str, Any],
    product: str,
    captured_ts: float,
) -> tuple[Schema, list[SchemaMarket], list[SchemaParticipant]]:
    """Map one `/playlists/` template into a `Schema` row plus its child
    `SchemaMarket` and `SchemaParticipant` rows. Pure function; no I/O.

    The slot mapping comes from `marketTemplates[i].odds[j].value` (an int);
    that slot is what later events' `oddValues[slot]` refer to.
    """
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
