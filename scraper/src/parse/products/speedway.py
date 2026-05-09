from __future__ import annotations

from src.parse.products.race_base import RaceProduct


class SpeedwayProduct(RaceProduct):
    name = "speedway"
    schema_class_types = frozenset({"SpeedwayParticipant"})
    event_participant_class_types = frozenset({"SpeedwayParticipant"})
    stats_class_types = frozenset({"SpeedwayEventBlockStats", "RaceEventBlockStats"})
    data_class_types = frozenset({"SpeedwayEventBlockData", "RaceEventBlockData"})
