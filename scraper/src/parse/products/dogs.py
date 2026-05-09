from __future__ import annotations

from typing import Any

from src.db.models import Event, EventRunner, Odds
from src.parse.products.base import (
    ExtractedRows,
    Product,
    _to_float,
    _to_int,
)


class DogsProduct(Product):
    name = "dogs"
    schema_class_types = frozenset({"DogParticipant"})
    event_participant_class_types = frozenset({"DogParticipant"})
    stats_class_types = frozenset({"DogEventBlockStats", "RaceEventBlockStats"})
    data_class_types = frozenset({"DogEventBlockData", "RaceEventBlockData"})

    def extract_event_rows(
        self,
        block: dict[str, Any],
        resource: str,
        captured_ts: float,
    ) -> ExtractedRows:
        e_block_id = _to_int(block.get("eBlockId"))
        schema_id = _to_int(block.get("playlistId"))
        if e_block_id is None or schema_id is None:
            return ([], [], [], [], [])

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
        )

        odds_rows: list[Odds] = []
        runners: list[EventRunner] = []

        if is_data and ev0_data:
            event.raw_data = ev0_data
            parts = ev0_data.get("participants") or []
            event.num_runners = len(parts)
            game = ev0_data.get("gameData") or {}
            event.track_condition = _to_float(game.get("trackCondition"))
            event.weather = game.get("weather")
            event.surface = game.get("surface")
            event.distance = _to_float(game.get("distance"))

            seen_runners: set[str] = set()
            for trap_index, p in enumerate(parts, start=1):
                if not isinstance(p, dict):
                    continue
                runner_id = str(p.get("id") or f"trap{trap_index}")
                if runner_id in seen_runners:
                    runner_id = f"{runner_id}#{trap_index}"
                seen_runners.add(runner_id)
                runners.append(EventRunner(
                    e_block_id=e_block_id,
                    runner_id=runner_id,
                    trap=trap_index,
                    name=p.get("name"),
                    prob=_to_float(p.get("prob")),
                    form=_to_float(p.get("form")),
                    star=_to_int(p.get("star")),
                    ability=_to_float(p.get("ability")),
                    speed=_to_float(p.get("speed")),
                    stamina=_to_float(p.get("stamina")),
                    wins=_to_float(p.get("wins")),
                    place=_to_float(p.get("place")),
                    pace=p.get("pace"),
                    forecast=_join_strs(p.get("forecast")),
                    raw=p,
                ))

            for slot, raw in enumerate(ev0_data.get("oddValues") or []):
                price = _to_float(raw)
                if price is None:
                    continue
                odds_rows.append(Odds(e_block_id=e_block_id, slot=slot, odds=price))

        if is_result and ev0_result:
            event.raw_result = ev0_result
            event.settled_ts = captured_ts
            result_data = ev0_result.get("data") or {}
            order = result_data.get("finalOrder")
            if isinstance(order, list):
                event.final_order = ",".join(str(x) for x in order)
                if len(order) >= 1:
                    event.winner_id = str(order[0])
                if len(order) >= 2:
                    event.second_id = str(order[1])
                if len(order) >= 3:
                    event.third_id = str(order[2])
            won = ev0_result.get("wonMarkets") or []
            if isinstance(won, list):
                event.won_markets = ",".join(str(w) for w in won)
                event.won_market_count = len(won)
            event.content_duration = _to_float(ev0.get("contentDuration"))
            event.media_id = result_data.get("mediaId")

        return ([event], odds_rows, [], runners, [])


def _join_strs(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, list):
        return ",".join(str(x) for x in v)
    return str(v)
