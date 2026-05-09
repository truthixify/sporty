from __future__ import annotations

from typing import Any

from src.parse.products.base import Product
from src.parse.products.dogs import DogsProduct
from src.parse.products.football import FootballProduct


_REGISTRY: list[Product] = [
    FootballProduct(),
    DogsProduct(),
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
    "Product",
    "all_products",
    "detect_for_event_block",
    "detect_for_schema_template",
]
