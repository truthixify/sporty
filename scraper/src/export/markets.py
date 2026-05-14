"""Helpers for humanizing the wire's odds slots and won_markets identifiers
in export payloads.

- Slot prices in the `odds` table are integer-keyed (`slot=0`, `slot=1`, ...).
  Joined to `schema_markets` they become `Match Result.Home`, `Both Teams Score.Yes`, etc.
- `won_markets` strings on the wire look like `Match_Result_Home`, `Over_Under_2_5_over`,
  `_2_1`, `Win_d3`. We try to match each against the schema's known
  (market_name, odd_name) combinations to produce structured records, falling
  back to the raw string when no match exists.
"""

from __future__ import annotations

from typing import Iterable

from src.db.models import Odds, SchemaMarket


def humanize_odds(
    odds: Iterable[Odds],
    markets_for_schema: list[SchemaMarket],
) -> dict[str, float]:
    """Return a `{market_name.odd_name: odds_value}` dict. Slots that don't
    have a known market_name/odd_name fall back to `slot_<n>`."""
    by_slot = {m.slot: m for m in markets_for_schema}
    out: dict[str, float] = {}
    for o in odds:
        sm = by_slot.get(o.slot)
        if sm is not None and sm.market_name and sm.odd_name:
            key = f"{sm.market_name}.{sm.odd_name}"
        else:
            key = f"slot_{o.slot}"
        # Prefer first-seen if dups happen (shouldn't, but defensive)
        out.setdefault(key, float(o.odds))
    return out


def parse_won_markets(
    raw_csv: str | None,
    markets_for_schema: list[SchemaMarket],
) -> list[dict[str, str]]:
    """Split the comma-joined `won_markets` string and parse each entry.

    Each output entry always has `raw`. When we can map it back to a known
    market via `schema_markets`, it also gets `market` and `outcome`.
    """
    if not raw_csv:
        return []
    parsed: list[dict[str, str]] = []
    for raw in raw_csv.split(","):
        raw = raw.strip()
        if not raw:
            continue
        match = _try_parse(raw, markets_for_schema)
        if match is not None:
            market_name, odd_name = match
            parsed.append({"raw": raw, "market": market_name, "outcome": odd_name})
        else:
            parsed.append({"raw": raw})
    return parsed


def _try_parse(
    raw: str,
    markets_for_schema: list[SchemaMarket],
) -> tuple[str, str] | None:
    """Find the (market_name, odd_name) whose underscored form matches `raw`.

    Wire format is `{market_name_with_spaces_as_underscores}_{odd_name_same}`,
    but some markets compress further. Strategy: produce candidate keys for
    every (market_name, odd_name) in the schema and match against `raw`.
    """
    # Build the lookup once per call. Cheap because schemas have ~50-200 markets.
    candidates: list[tuple[str, str, str]] = []
    for sm in markets_for_schema:
        if not sm.market_name or not sm.odd_name:
            continue
        m_norm = _normalize(sm.market_name)
        o_norm = _normalize(sm.odd_name)
        candidates.append((f"{m_norm}_{o_norm}", sm.market_name, sm.odd_name))

    target = raw  # already normalized as it came off the wire

    # Exact match first
    for combined, market, outcome in candidates:
        if combined == target:
            return market, outcome
    # Prefix match: market_name underscored is a prefix of raw, remainder is the odd_name part
    for combined, market, outcome in candidates:
        market_prefix = combined.rsplit("_", 1)[0] if "_" in combined else combined
        if target.startswith(market_prefix + "_"):
            tail = target[len(market_prefix) + 1:]
            if _normalize(outcome) == tail:
                return market, outcome
    return None


def _normalize(s: str) -> str:
    """Replace whitespace/dashes with underscores so wire strings and
    schema-market labels can be compared apples-to-apples."""
    return "_".join(s.split())


def preload_markets(session, schema_ids: Iterable[int]) -> dict[int, list[SchemaMarket]]:
    """Fetch all SchemaMarket rows for the given schemas in one query and
    bucket them by schema_id. Caller can then humanize odds across many
    events without re-querying."""
    from sqlalchemy import select

    out: dict[int, list[SchemaMarket]] = {sid: [] for sid in schema_ids}
    if not out:
        return out
    rows = session.scalars(
        select(SchemaMarket).where(SchemaMarket.schema_id.in_(out.keys()))
    ).all()
    for r in rows:
        out.setdefault(r.schema_id, []).append(r)
    return out
