from __future__ import annotations

from src.parse.products.race_base import RaceProduct


class DogsProduct(RaceProduct):
    name = "dogs"
    schema_class_types = frozenset({"DogParticipant"})
    event_participant_class_types = frozenset({"DogParticipant"})
    stats_class_types = frozenset({"DogEventBlockStats", "RaceEventBlockStats"})
    data_class_types = frozenset({"DogEventBlockData", "RaceEventBlockData"})
