from __future__ import annotations

from src.parse.products.races import RaceProduct


class HorsesProduct(RaceProduct):
    name = "horses"
    schema_class_types = frozenset({"HorseParticipant"})
    event_participant_class_types = frozenset({"HorseParticipant"})
    stats_class_types = frozenset({"HorseEventBlockStats", "RaceEventBlockStats"})
    data_class_types = frozenset({"HorseEventBlockData", "RaceEventBlockData"})
