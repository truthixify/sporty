from __future__ import annotations

import pytest

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


# --- humanize_odds ----------------------------------------------------------

def test_humanize_odds_uses_market_name_dot_odd_name() -> None:
    odds = [Odds(e_block_id=1, slot=0, odds=1.85), Odds(e_block_id=1, slot=51, odds=1.72)]
    assert humanize_odds(odds, _markets()) == {
        "Match Result.Home": 1.85,
        "Both Teams Score.Yes": 1.72,
    }


def test_humanize_odds_falls_back_to_slot_n_for_unknown() -> None:
    assert humanize_odds([Odds(e_block_id=1, slot=999, odds=2.5)], _markets()) == {"slot_999": 2.5}


# --- Pass 1: schema-driven exact match -------------------------------------

def test_parse_full_match_against_schema() -> None:
    out = parse_won_markets("Match_Result_Home,Both_Teams_Score_Yes", _markets())
    assert out == [
        {"raw": "Match_Result_Home", "market": "Match Result", "outcome": "Home"},
        {"raw": "Both_Teams_Score_Yes", "market": "Both Teams Score", "outcome": "Yes"},
    ]


# --- Pass 2: schema odd_name only ------------------------------------------

def test_parse_odd_name_only_falls_back_to_match_result() -> None:
    """Single-token wonMarkets like 'Draw' should map to Match Result.Draw."""
    out = parse_won_markets("Draw,Home,Away", _markets())
    assert out[0]["market"] == "Match Result" and out[0]["outcome"] == "Draw"
    assert out[1]["market"] == "Match Result" and out[1]["outcome"] == "Home"
    assert out[2]["market"] == "Match Result" and out[2]["outcome"] == "Away"


# --- Pass 3: pattern decoders ----------------------------------------------

@pytest.mark.parametrize("raw,market,outcome", [
    ("_2_1",  "Correct Score", "2-1"),
    ("_0_0",  "Correct Score", "0-0"),
    ("_4_2",  "Correct Score", "4-2"),
    ("_10_0", "Correct Score", "10-0"),
])
def test_decode_correct_score(raw: str, market: str, outcome: str) -> None:
    out = parse_won_markets(raw, _markets())
    assert out[0]["market"] == market and out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,outcome", [
    ("_0_Goals", "0 goals"),
    ("_3_Goals", "3 goals"),
])
def test_decode_total_goals(raw: str, outcome: str) -> None:
    out = parse_won_markets(raw, _markets())
    assert out[0]["market"] == "Total Goals" and out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,outcome", [
    ("HomeHome", "Home/Home"),
    ("DrawDraw", "Draw/Draw"),
    ("AwayAway", "Away/Away"),
    ("HomeDraw", "Home/Draw"),
    ("DrawHome", "Draw/Home"),
    ("HomeAway", "Home/Away"),
    ("AwayHome", "Away/Home"),
])
def test_decode_ht_ft(raw: str, outcome: str) -> None:
    out = parse_won_markets(raw, _markets())
    assert out[0]["market"] == "Half Time / Full Time"
    assert out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,outcome", [
    ("Home_Draw", "Home or Draw"),
    ("Draw_Home", "Home or Draw"),  # normalized order
    ("Draw_Away", "Draw or Away"),
    ("Away_Draw", "Draw or Away"),  # normalized order
    ("Home_Away", "Home or Away"),
    ("Away_Home", "Home or Away"),  # normalized order
])
def test_decode_double_chance(raw: str, outcome: str) -> None:
    out = parse_won_markets(raw, _markets())
    assert out[0]["market"] == "Double Chance"
    assert out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,market,outcome", [
    ("Over_2_5_over",       "Over/Under 2.5",  "Over"),
    ("Over_2_5_under",      "Over/Under 2.5",  "Under"),
    ("Over_Under_3_5_over", "Over/Under 3.5",  "Over"),
    ("Over_Under_0_5_under","Over/Under 0.5",  "Under"),
])
def test_decode_over_under(raw: str, market: str, outcome: str) -> None:
    out = parse_won_markets(raw, _markets())
    assert out[0]["market"] == market and out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,outcome", [
    ("BTTS_Yes",              "Yes"),
    ("BTTS_No",               "No"),
    ("Both_Teams_Score_Yes",  "Yes"),
])
def test_decode_btts_via_pattern(raw: str, outcome: str) -> None:
    # Use a markets list WITHOUT the BTTS schema rows so the pattern fallback fires
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == "Both Teams Score"
    assert out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,market,outcome", [
    ("Win_d3",            "Win",          "d3"),
    ("Place_d1",          "Place",        "d1"),
    ("Show_d2",           "Show",         "d2"),
    ("Forecast_d3_d1",    "Forecast",     "d3/d1"),
    ("Tricast_d3_d1_d2",  "Tricast",      "d3/d1/d2"),
    # Lowercase, trap-number based
    ("win_3",             "Win",          "trap 3"),
    ("show_1",            "Show",         "trap 1"),
    ("place_1",           "Place",        "trap 1"),
    ("fourth_5",          "Fourth Place", "trap 5"),
    ("fifth_3",           "Fifth Place",  "trap 3"),
    ("quinella_2_3",      "Quinella",     "2/3"),
    ("forecast_2_3",      "Forecast",     "2/3"),
])
def test_decode_race_bets(raw: str, market: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == market and out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,market", [
    ("system_four_any",  "System Four Any"),
    ("system_five_all",  "System Five All"),
    ("system_six_any",   "System Six Any"),
])
def test_decode_race_system_variants(raw: str, market: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == market


@pytest.mark.parametrize("raw,market,outcome", [
    ("Home_Plus_0_5",  "Asian Handicap", "Home +0.5"),
    ("Away_Plus_2_25", "Asian Handicap", "Away +2.25"),
    ("Home_Plus_3",    "Asian Handicap", "Home +3"),
    ("Home_Minus_1_5", "Asian Handicap", "Home -1.5"),
])
def test_decode_asian_handicap(raw: str, market: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == market and out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,market,outcome", [
    ("_0_3_Goals", "Total Goals Range", "0-3 goals"),
    ("_2_5_Goals", "Total Goals Range", "2-5 goals"),
    ("_0_0_Goals", "Total Goals",       "0 goals"),  # collapses lo==hi to single
])
def test_decode_goal_range(raw: str, market: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == market and out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,market,outcome", [
    ("Home_Under_2_5",        "Home Under/Over 2.5", "Under"),
    ("Home_Over_2_5",         "Home Under/Over 2.5", "Over"),
    ("Away_Scores_Under_3_5", "Away Under/Over 3.5", "Under"),
    ("Away_Scores_Over_1_5",  "Away Under/Over 1.5", "Over"),
    ("Home_0_Goals",          "Home Total Goals",    "0 goals"),
    ("Home_2_Goals",          "Home Total Goals",    "2 goals"),
    ("Away_0_Goals",          "Away Total Goals",    "0 goals"),
    ("Home_Nil",              "Home Total Goals",    "0 goals"),
    ("Away_Nil",              "Away Total Goals",    "0 goals"),
])
def test_decode_team_total_markets(raw: str, market: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == market and out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,outcome", [
    ("Home_By_1", "Home by 1"),
    ("Away_By_2", "Away by 2"),
    ("Home_By_3", "Home by 3"),
])
def test_decode_winning_margin(raw: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == "Winning Margin"
    assert out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,market,outcome", [
    ("Home_Over_2_5_GG", "Home Over 2.5 + BTTS",      "Home"),
    ("Away_Under_1_5_NG","Away Under 1.5 + No Goals", "Away"),
])
def test_decode_team_over_under_with_gg(raw: str, market: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == market and out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,outcome", [
    ("gg", "Yes"),
    ("ng", "No"),
    ("GG", "Yes"),
])
def test_decode_btts_shorthand(raw: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == "Both Teams Score"
    assert out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,market,outcome", [
    ("HT_under075", "Half-time Over/Under 0.75", "Under"),
    ("HT_under10",  "Half-time Over/Under 1.0",  "Under"),
    ("HT_under125", "Half-time Over/Under 1.25", "Under"),
    ("HT_under20",  "Half-time Over/Under 2.0",  "Under"),
    ("HT_over125",  "Half-time Over/Under 1.25", "Over"),
])
def test_decode_ht_under(raw: str, market: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == market and out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,market,outcome", [
    ("under_0_5",  "Over/Under 0.5",  "Under"),
    ("under_2_25", "Over/Under 2.25", "Under"),
    ("under_3_0",  "Over/Under 3.0",  "Under"),
    ("over_2_5",   "Over/Under 2.5",  "Over"),
])
def test_decode_under_only(raw: str, market: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == market and out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,outcome", [
    ("_1H_Home", "Home"),
    ("_1H_Draw", "Draw"),
    ("_1H_Away", "Away"),
])
def test_decode_first_half_outcome(raw: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == "First Half Result" and out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,market,outcome", [
    ("_1x2_Draw_Scores_Under_2_5", "1X2 + Under 2.5", "Draw"),
    ("_1x2_Home_Scores_Under_1_5", "1X2 + Under 1.5", "Home"),
    ("_1x2_Home_Scores_Over_1_5",  "1X2 + Over 1.5",  "Home"),
    ("_1x2_Away_Scores_Over_2_5",  "1X2 + Over 2.5",  "Away"),
])
def test_decode_combo_1x2_over_under(raw: str, market: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == market and out[0]["outcome"] == outcome


def test_decode_no_goals_shorthand() -> None:
    out = parse_won_markets("nog,NOG", [])
    assert all(o["market"] == "No Goals" and o["outcome"] == "Yes" for o in out)


@pytest.mark.parametrize("raw,outcome", [
    ("Draw_No_Goal_MV", "Draw"),
    ("Home_No_Goal", "Home"),
])
def test_decode_outcome_no_goal_combo(raw: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == "1X2 + No Goal"
    assert out[0]["outcome"] == outcome


def test_decode_outcome_under_no_goal_only_for_draw() -> None:
    """Restricted to Draw because Home/Away under-N + NG is more naturally
    read as `team scores under N + no goals` (caught by team_over_under_with_gg)."""
    out = parse_won_markets("Draw_Under_2_5_NG", [])
    assert out[0]["market"] == "1X2 + Under 2.5 + No Goal"
    assert out[0]["outcome"] == "Draw"


@pytest.mark.parametrize("raw,market,outcome", [
    ("Draw_Under_2_5_GG", "1X2 + Under 2.5 + BTTS", "Draw"),
    ("Draw_Over_2_5_GG",  "1X2 + Over 2.5 + BTTS",  "Draw"),
])
def test_decode_outcome_under_btts(raw: str, market: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == market and out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,outcome", [
    ("Home_By_3_More", "Home by 3 or more"),
    ("Away_By_2_More", "Away by 2 or more"),
])
def test_decode_winning_margin_or_more(raw: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == "Winning Margin"
    assert out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,outcome", [
    ("Draw_Goal_MV", "Draw"),
])
def test_decode_outcome_goal_combo(raw: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == "1X2 + Goal" and out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,market", [
    ("trifecta",      "Trifecta"),
    ("system_two",    "System Two"),
    ("system_three",  "System Three"),
    ("three_any",     "Three Any"),
    ("under",         "Under"),
    ("odd",           "Odd"),
    ("even",          "Even"),
])
def test_decode_race_market_type(raw: str, market: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == market


@pytest.mark.parametrize("raw,market,outcome", [
    # show_<digit> / place_<digit> are now treated as trap-based bets
    # (more specific than the bare market-type interpretation)
    ("show_3",        "Show",  "trap 3"),
    ("place_2",       "Place", "trap 2"),
    ("second_3",      "Second Place", "trap 3"),
    ("third_4",       "Third Place",  "trap 4"),
    ("exacta_3_6",    "Exacta", "3/6"),
    ("exacta_2_1",    "Exacta", "2/1"),
])
def test_decode_race_position_bets(raw: str, market: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == market and out[0]["outcome"] == outcome


@pytest.mark.parametrize("raw,outcome", [
    ("Draw_0", "Draw (0 goals)"),
    ("Home_2", "Home (2 goals)"),
])
def test_decode_outcome_with_score(raw: str, outcome: str) -> None:
    out = parse_won_markets(raw, [])
    assert out[0]["market"] == "1X2 + Total Goals"
    assert out[0]["outcome"] == outcome


def test_unknown_strings_remain_raw_only() -> None:
    out = parse_won_markets("CompletelyUnknownThing,RandomThing_Other", [])
    assert all(set(entry.keys()) == {"raw"} for entry in out)


def test_empty_inputs() -> None:
    assert parse_won_markets(None, []) == []
    assert parse_won_markets("", []) == []
    assert parse_won_markets(",,", []) == []


def test_pattern_decoder_runs_when_schema_does_not_match() -> None:
    """The schema doesn't have a Correct Score market, but the wire still
    sends `_2_1`. Pattern decoder should pick it up."""
    out = parse_won_markets("_2_1", _markets())
    assert out[0]["market"] == "Correct Score"
    assert out[0]["outcome"] == "2-1"
