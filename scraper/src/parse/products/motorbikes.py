from __future__ import annotations

from src.parse.products.race_base import RaceProduct


class MotorbikesProduct(RaceProduct):
    name = "motorbikes"
    schema_class_types = frozenset({"MotorbikeParticipant", "MotorbikesParticipant"})
    event_participant_class_types = frozenset({"MotorbikeParticipant", "MotorbikesParticipant"})
    stats_class_types = frozenset({
        "MotorbikeEventBlockStats", "MotorbikesEventBlockStats", "RaceEventBlockStats",
    })
    data_class_types = frozenset({
        "MotorbikeEventBlockData", "MotorbikesEventBlockData", "RaceEventBlockData",
    })
