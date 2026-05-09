from __future__ import annotations

from typing import Any

from src.parse.products.base import Product
from src.parse.products.dogs import DogsProduct
from src.parse.products.football import FootballProduct
from src.parse.products.horses import HorsesProduct
from src.parse.products.mma import MmaProduct
from src.parse.products.motorbikes import MotorbikesProduct
from src.parse.products.races import RaceProduct
from src.parse.products.speedway import SpeedwayProduct


# FootballProduct first because its event_participant class types are
# disjoint from races. Race-style products are listed in stable order; per-event
# dispatch is unambiguous because each one's participantClassType is unique.
_REGISTRY: list[Product] = [
    FootballProduct(),
    DogsProduct(),
    HorsesProduct(),
    SpeedwayProduct(),
    MotorbikesProduct(),
    MmaProduct(),
]


def all_products() -> list[Product]:
    return list(_REGISTRY)


def detect_for_event_block(block: dict[str, Any]) -> Product | None:
    for p in _REGISTRY:
        if p.matches_event_block(block):
            return p
    return None


def detect_for_schema_template(tpl: dict[str, Any]) -> Product | None:
    for p in _REGISTRY:
        if p.matches_schema(tpl):
            return p
    return None


__all__ = [
    "DogsProduct",
    "FootballProduct",
    "HorsesProduct",
    "MmaProduct",
    "MotorbikesProduct",
    "Product",
    "RaceProduct",
    "SpeedwayProduct",
    "all_products",
    "detect_for_event_block",
    "detect_for_schema_template",
]
