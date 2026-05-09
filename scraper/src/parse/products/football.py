from __future__ import annotations

from typing import Any

from src.db.models import (
    Event,
    EventParticipantFootball,
    EventRunner,
    Odds,
    Standing,
)
from src.parse.products.base import (
    ExtractedRows,
    Product,
    _to_float,
    _to_int,
)


class FootballProduct(Product):
    name = "football"
    schema_class_types = frozenset({"FbParticipant", "FootballParticipant"})
    event_participant_class_types = frozenset({"FbParticipant", "FootballParticipant"})
    stats_class_types = frozenset({"FbEventBlockStats", "FootballEventBlockStats"})
    data_class_types = frozenset({"FbEventBlockData", "FootballEventBlockData"})

    def extract_event_rows(
        self,
        block: dict[str, Any],
        resource: str,
        captured_ts: float,
    ) -> ExtractedRows:
        e_block_id = _to_int(block.get("eBlockId"))
        if e_block_id is None:
            return ([], [], [], [], [])
        schema_id = _to_int(block.get("playlistId"))
        if schema_id is None:
            return ([], [], [], [], [])

        block_data = block.get("data") or {}
        events_inner = block.get("events") or [{}]
        ev0 = events_inner[0] or {}
        ev0_data = ev0.get("data") or {}
        ev0_result = ev0.get("result") or {}

        is_data = "/event/data" in resource
        is_result = "/event/result" in resource

        event = Event(
            e_block_id=e_block_id,
            schema_id=schema_id,
            product=self.name,
            server_status=block.get("serverStatus"),
            event_time=block.get("eventTime"),
            captured_ts=captured_ts,
            phase=block_data.get("phase"),
            match_day=_to_int(block_data.get("matchDay")),
            leg_order=_to_int(block_data.get("legOrder")),
            week_day=_to_int(block_data.get("weekDay")),
            champ_id=_to_int(block_data.get("champId")),
        )

        odds_rows: list[Odds] = []
        participants: list[EventParticipantFootball] = []

        if is_data and ev0_data:
            event.raw_data = ev0_data
            parts = ev0_data.get("participants") or []
            for side, p in zip(("home", "away"), parts[:2]):
                team_id = p.get("id") if isinstance(p, dict) else None
                if not team_id:
                    continue
                participants.append(EventParticipantFootball(
                    e_block_id=e_block_id,
                    side=side,
                    team_id=str(team_id),
                    stars=_to_float(p.get("stars")),
                ))
            if len(parts) >= 2:
                event.home_team_id = str(parts[0].get("id"))
                event.away_team_id = str(parts[1].get("id"))

            for slot, raw in enumerate(ev0_data.get("oddValues") or []):
                price = _to_float(raw)
                if price is None:
                    continue
                odds_rows.append(Odds(e_block_id=e_block_id, slot=slot, odds=price))

        if is_result and ev0_result:
            event.raw_result = ev0_result
            event.settled_ts = captured_ts
            outcome = ev0_result.get("finalOutcome")
            if isinstance(outcome, list) and len(outcome) >= 2:
                event.home_score = _to_int(outcome[0])
                event.away_score = _to_int(outcome[1])
            won = ev0_result.get("wonMarkets") or []
            if isinstance(won, list):
                event.won_markets = ",".join(str(w) for w in won)
                event.won_market_count = len(won)
            event.content_duration = _to_float(ev0.get("contentDuration"))

        return ([event], odds_rows, participants, [], [])

    def extract_stats_rows(
        self,
        block: dict[str, Any],
        captured_ts: float,
    ) -> list[Standing]:
        e_block_id = _to_int(block.get("eBlockId"))
        if e_block_id is None:
            return []
        block_data = block.get("data") or {}
        match_day = _to_int(block_data.get("matchDay"))
        stats = block.get("stats") or {}
        groups = stats.get("groupClassification") or []

        out: list[Standing] = []
        seen: set[tuple[int, str]] = set()
        for group in groups:
            for entry in (group or {}).get("entries") or []:
                team_id = entry.get("participantId") or entry.get("teamId") or entry.get("id")
                if not team_id:
                    continue
                team_id = str(team_id)
                if (e_block_id, team_id) in seen:
                    continue
                seen.add((e_block_id, team_id))
                history = entry.get("history") or []
                history_csv = ",".join(str(h) for h in history) if isinstance(history, list) else None
                out.append(Standing(
                    e_block_id=e_block_id,
                    team_id=team_id,
                    match_day=match_day,
                    ranking=_to_int(entry.get("ranking") or entry.get("rank")),
                    points=_to_int(entry.get("points")),
                    wins=_to_int(entry.get("wins")),
                    draws=_to_int(entry.get("draws")),
                    losses=_to_int(entry.get("losses")),
                    goals_for=_to_int(entry.get("goalsFor")),
                    goals_against=_to_int(entry.get("goalsAgainst")),
                    goal_diff=_to_int(entry.get("goalDiff")),
                    history=history_csv,
                ))
        return out
