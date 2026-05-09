from __future__ import annotations

from src.parse.products.races import RaceProduct


class MmaProduct(RaceProduct):
    """Mixed martial arts. Two fighters per event; result has a winner. The
    wire shape is similar enough to races that we reuse `RaceProduct`'s
    extractor (the runner table holds the fighters, `winner_id` is the
    finisher of `finalOrder[0]`)."""

    name = "mma"
    schema_class_types = frozenset({"MmaParticipant"})
    event_participant_class_types = frozenset({"MmaParticipant"})
    stats_class_types = frozenset({"MmaEventBlockStats"})
    data_class_types = frozenset({"MmaEventBlockData"})
