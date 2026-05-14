from __future__ import annotations

from src.db.models import Odds, SchemaMarket
from src.export.markets import humanize_odds, parse_won_markets


def _markets() -> list[SchemaMarket]:
    return [
        SchemaMarket(schema_id=1, slot=0, market_id="m1", market_name="Match Result", odd_id="o1", odd_name="Home"),
        SchemaMarket(schema_id=1, slot=1, market_id="m1", market_name="Match Result", odd_id="o2", odd_name="Away"),
        SchemaMarket(schema_id=1, slot=2, market_id="m1", market_name="Match Result", odd_id="o3", odd_name="Draw"),
        SchemaMarket(schema_id=1, slot=50, market_id="m2", market_name="Both Teams Score", odd_id="o4", odd_name="No"),
        SchemaMarket(schema_id=1, slot=51, market_id="m2", market_name="Both Teams Score", odd_id="o5", odd_name="Yes"),
    ]


def test_humanize_odds_uses_market_name_dot_odd_name() -> None:
    odds = [Odds(e_block_id=1, slot=0, odds=1.85), Odds(e_block_id=1, slot=51, odds=1.72)]
    out = humanize_odds(odds, _markets())
    assert out == {"Match Result.Home": 1.85, "Both Teams Score.Yes": 1.72}


def test_humanize_odds_falls_back_to_slot_n_for_unknown() -> None:
    odds = [Odds(e_block_id=1, slot=999, odds=2.5)]
    out = humanize_odds(odds, _markets())
    assert out == {"slot_999": 2.5}


def test_parse_won_markets_exact_match() -> None:
    out = parse_won_markets("Match_Result_Home,Both_Teams_Score_Yes", _markets())
    assert out == [
        {"raw": "Match_Result_Home", "market": "Match Result", "outcome": "Home"},
        {"raw": "Both_Teams_Score_Yes", "market": "Both Teams Score", "outcome": "Yes"},
    ]


def test_parse_won_markets_unparseable_falls_back_to_raw_only() -> None:
    out = parse_won_markets("_2_1,Some_Weird_Thing", _markets())
    # Both should land as raw-only since neither matches a known (market, odd) pair.
    assert all(set(entry.keys()) == {"raw"} for entry in out)
    assert [e["raw"] for e in out] == ["_2_1", "Some_Weird_Thing"]


def test_parse_won_markets_empty_input() -> None:
    assert parse_won_markets(None, _markets()) == []
    assert parse_won_markets("", _markets()) == []
    assert parse_won_markets(",,", _markets()) == []
