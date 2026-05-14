"""Helpers for humanizing the wire's odds slots and won_markets identifiers
in export payloads.

- Slot prices in the `odds` table are integer-keyed (`slot=0`, `slot=1`, ...).
  Joined to `schema_markets` they become `Match Result.Home`, `Both Teams Score.Yes`, etc.
- `won_markets` strings on the wire are short identifiers like `Match_Result_Home`,
  `_2_1`, `DrawDraw`, `Home_Draw`, `Win_d3`. We try to map each entry back to
  a `(market, outcome)` pair through three passes:

    1. exact match against `{market_name}_{odd_name}` for the schema
    2. odd_name-only match (catches single-token wins like `Draw`, `Home`)
    3. structural pattern decoders for known market families (Correct Score,
       HT/FT, Double Chance, Over/Under, Total Goals, BTTS, race Win/Place/Show)

Each entry always includes `raw`. Successful matches add `market` and `outcome`.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable

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
        out.setdefault(key, float(o.odds))
    return out


def parse_won_markets(
    raw_csv: str | None,
    markets_for_schema: list[SchemaMarket],
) -> list[dict[str, str]]:
    """Split the comma-joined `won_markets` string and parse each entry."""
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
    """Three passes: schema exact match, schema odd-name match, pattern decoders."""
    full = _try_schema_full_match(raw, markets_for_schema)
    if full is not None:
        return full
    odd_only = _try_schema_odd_only(raw, markets_for_schema)
    if odd_only is not None:
        return odd_only
    for decoder in _PATTERN_DECODERS:
        result = decoder(raw)
        if result is not None:
            return result
    return None


def _try_schema_full_match(
    raw: str,
    markets: list[SchemaMarket],
) -> tuple[str, str] | None:
    """Match raw against `{market_name}_{odd_name}` for any (market, odd) in the schema."""
    for sm in markets:
        if not sm.market_name or not sm.odd_name:
            continue
        m_norm = _normalize(sm.market_name)
        o_norm = _normalize(sm.odd_name)
        combined = f"{m_norm}_{o_norm}"
        if combined == raw:
            return sm.market_name, sm.odd_name
        # Tolerate a leading market_name without separator before the odd_name
        # (some wire variants drop the underscore between market and outcome).
        if raw == m_norm + o_norm:
            return sm.market_name, sm.odd_name
    return None


def _try_schema_odd_only(
    raw: str,
    markets: list[SchemaMarket],
) -> tuple[str, str] | None:
    """Match raw against just `odd_name`. First match wins. Useful when the
    wire shorthand drops the market name (e.g. wonMarket = `Draw` for
    Match Result.Draw)."""
    for sm in markets:
        if not sm.odd_name:
            continue
        if _normalize(sm.odd_name) == raw:
            return sm.market_name or "?", sm.odd_name
    return None


# --- Pattern decoders ---------------------------------------------------------
#
# These take the raw wonMarket string and try to recognize a known market
# family by structure alone. Order matters: more specific patterns should run
# before more permissive ones. Each returns (market_label, outcome_label) on
# match, None otherwise.

_FB_OUTCOMES = ("Home", "Draw", "Away")


def _decode_correct_score(raw: str) -> tuple[str, str] | None:
    # "_2_1", "_0_0" => Correct Score 2-1, 0-0
    m = re.fullmatch(r"_(\d+)_(\d+)", raw)
    if m:
        return "Correct Score", f"{m.group(1)}-{m.group(2)}"
    return None


def _decode_total_goals(raw: str) -> tuple[str, str] | None:
    # "_3_Goals" => Total Goals "3 goals"; also tolerate plural form variants
    m = re.fullmatch(r"_(\d+)_Goals?", raw)
    if m:
        return "Total Goals", f"{m.group(1)} goals"
    return None


def _decode_ht_ft(raw: str) -> tuple[str, str] | None:
    # Two concatenated outcomes from {Home, Draw, Away}: "HomeHome", "DrawHome", ...
    m = re.fullmatch(
        r"(Home|Draw|Away)(Home|Draw|Away)",
        raw,
    )
    if m:
        return "Half Time / Full Time", f"{m.group(1)}/{m.group(2)}"
    return None


def _decode_double_chance(raw: str) -> tuple[str, str] | None:
    # Two outcomes joined by underscore: "Home_Draw", "Draw_Away", "Home_Away"
    m = re.fullmatch(r"(Home|Draw|Away)_(Home|Draw|Away)", raw)
    if m and m.group(1) != m.group(2):
        a, b = m.group(1), m.group(2)
        # Normalize order: 1X (Home/Draw), X2 (Draw/Away), 12 (Home/Away)
        order = {"Home": 0, "Draw": 1, "Away": 2}
        if order[a] > order[b]:
            a, b = b, a
        return "Double Chance", f"{a} or {b}"
    return None


def _decode_over_under(raw: str) -> tuple[str, str] | None:
    # "Over_Under_2_5_over", "Over_Under_2_5_under" - the canonical form
    m = re.fullmatch(r"Over_Under_(\d+)_(\d+)_(over|under)", raw)
    if m:
        line = f"{m.group(1)}.{m.group(2)}"
        side = m.group(3).capitalize()
        return f"Over/Under {line}", side
    # "Over_2_5_over" - shorter variant
    m = re.fullmatch(r"Over_(\d+)_(\d+)_(over|under)", raw)
    if m:
        line = f"{m.group(1)}.{m.group(2)}"
        side = m.group(3).capitalize()
        return f"Over/Under {line}", side
    return None


def _decode_btts(raw: str) -> tuple[str, str] | None:
    # "BTTS_Yes", "BTTS_No" or full "Both_Teams_Score_Yes"/"...No"
    m = re.fullmatch(r"BTTS_(Yes|No)", raw, re.IGNORECASE)
    if m:
        return "Both Teams Score", m.group(1).capitalize()
    m = re.fullmatch(r"Both_Teams_Score_(Yes|No)", raw, re.IGNORECASE)
    if m:
        return "Both Teams Score", m.group(1).capitalize()
    return None


def _decode_race_bets(raw: str) -> tuple[str, str] | None:
    # Capitalized, runner-id-based: "Win_d3", "Place_d1", "Show_d2"
    m = re.fullmatch(r"(Win|Place|Show)_(.+)", raw)
    if m:
        return m.group(1), m.group(2)
    # Lowercase with trap-number suffix: "win_3", "show_1", "place_1"
    m = re.fullmatch(r"(win|show|place)_(\d+)", raw)
    if m:
        return m.group(1).capitalize(), f"trap {m.group(2)}"
    # Higher-position bets: "second_3", "third_4", "fourth_5", "fifth_3"
    # (trap N finishes Nth)
    m = re.fullmatch(r"(second|third|fourth|fifth|sixth|seventh|eighth)_(\d+)", raw)
    if m:
        return f"{m.group(1).capitalize()} Place", f"trap {m.group(2)}"
    # Exacta (top 2 in exact order): "exacta_3_6", "exacta_2_1"
    m = re.fullmatch(r"exacta_(\w+)_(\w+)", raw)
    if m:
        return "Exacta", f"{m.group(1)}/{m.group(2)}"
    # Forecasts / tricasts in either casing, with runner ids OR trap numbers
    m = re.fullmatch(r"[Ff]orecast_(\w+)_(\w+)", raw)
    if m:
        return "Forecast", f"{m.group(1)}/{m.group(2)}"
    m = re.fullmatch(r"[Tt]ricast_(\w+)_(\w+)_(\w+)", raw)
    if m:
        return "Tricast", f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    # Quinella (top 2 in any order)
    m = re.fullmatch(r"quinella_(\w+)_(\w+)", raw)
    if m:
        return "Quinella", f"{m.group(1)}/{m.group(2)}"
    return None


def _decode_team_total_goals(raw: str) -> tuple[str, str] | None:
    # "Home_0_Goals" / "Away_2_Goals" - team scored exactly N goals
    m = re.fullmatch(r"(Home|Away)_(\d+)_Goals", raw)
    if m:
        return f"{m.group(1)} Total Goals", f"{m.group(2)} goals"
    # "Home_Nil" / "Away_Nil" - alt phrasing for "scored 0"
    m = re.fullmatch(r"(Home|Away)_Nil", raw)
    if m:
        return f"{m.group(1)} Total Goals", "0 goals"
    return None


def _decode_team_over_under(raw: str) -> tuple[str, str] | None:
    # "Home_Under_2_5", "Home_Over_2_5", "Away_Scores_Under_3_5", "Away_Scores_Over_1_5"
    m = re.fullmatch(r"(Home|Away)(?:_Scores)?_(Under|Over)_(\d+)_(\d+)", raw)
    if m:
        side, ou, intpart, frac = m.group(1), m.group(2), m.group(3), m.group(4)
        line = f"{intpart}.{frac}"
        return f"{side} Under/Over {line}", ou
    return None


def _decode_team_winning_margin(raw: str) -> tuple[str, str] | None:
    # "Home_By_1" / "Away_By_2" - winning margin (team wins by exactly N goals)
    m = re.fullmatch(r"(Home|Away)_By_(\d+)", raw)
    if m:
        return "Winning Margin", f"{m.group(1)} by {m.group(2)}"
    return None


def _decode_asian_handicap(raw: str) -> tuple[str, str] | None:
    # "Home_Plus_0_5", "Away_Plus_2_25", "Home_Plus_3" - Asian handicap (positive)
    # The handicap is the part after Plus_, encoded as either an integer or X_Y
    # for X.Y. Also tolerate Minus for the negative side.
    m = re.fullmatch(r"(Home|Away)_(Plus|Minus)_(\d+)(?:_(\d+))?", raw)
    if m:
        side, sign, intpart, frac = m.group(1), m.group(2), m.group(3), m.group(4)
        line = f"{intpart}.{frac}" if frac else intpart
        sign_char = "+" if sign == "Plus" else "-"
        return "Asian Handicap", f"{side} {sign_char}{line}"
    return None


def _decode_goal_range(raw: str) -> tuple[str, str] | None:
    # "_X_Y_Goals" - range of goals, e.g. _0_3_Goals = 0 to 3 goals total
    m = re.fullmatch(r"_(\d+)_(\d+)_Goals", raw)
    if m:
        lo, hi = m.group(1), m.group(2)
        if lo == hi:
            return "Total Goals", f"{lo} goals"
        return "Total Goals Range", f"{lo}-{hi} goals"
    return None


def _decode_under_only(raw: str) -> tuple[str, str] | None:
    # "under_2_5", "under_2_25", "under_3_0" - plain Under <line>
    m = re.fullmatch(r"under_(\d+)_(\d+)", raw)
    if m:
        return f"Over/Under {m.group(1)}.{m.group(2)}", "Under"
    m = re.fullmatch(r"over_(\d+)_(\d+)", raw)
    if m:
        return f"Over/Under {m.group(1)}.{m.group(2)}", "Over"
    return None


def _decode_ht_under(raw: str) -> tuple[str, str] | None:
    # "HT_under075", "HT_under10", "HT_under125", "HT_under20" - half-time
    # under <line>, with the line encoded as concatenated digits (no separator):
    #  - 1 digit  -> X.0   (e.g. "5" -> 0.5? unusual)
    #  - 2 digits -> X.Y   (e.g. "10" -> 1.0, "20" -> 2.0)
    #  - 3 digits -> X.YZ  (e.g. "075" -> 0.75, "125" -> 1.25, "175" -> 1.75)
    m = re.fullmatch(r"HT_(over|under)(\d{1,3})", raw)
    if m:
        side, digits = m.group(1).capitalize(), m.group(2)
        if len(digits) == 1:
            line = f"0.{digits}"
        elif len(digits) == 2:
            line = f"{digits[0]}.{digits[1]}"
        else:
            line = f"{digits[0]}.{digits[1:]}"
        return f"Half-time Over/Under {line}", side
    return None


def _decode_first_half_outcome(raw: str) -> tuple[str, str] | None:
    # "_1H_Home", "_1H_Draw", "_1H_Away" - first-half match result
    m = re.fullmatch(r"_1H_(Home|Draw|Away)", raw)
    if m:
        return "First Half Result", m.group(1)
    return None


def _decode_combo_1x2_under_over(raw: str) -> tuple[str, str] | None:
    # "_1x2_Draw_Scores_Under_2_5" / "_1x2_Home_Scores_Over_1_5" - combined
    # 1X2 outcome + total under/over.
    m = re.fullmatch(r"_1x2_(Home|Draw|Away)_Scores_(Under|Over)_(\d+)_(\d+)", raw)
    if m:
        outcome, ou, intpart, frac = m.group(1), m.group(2), m.group(3), m.group(4)
        line = f"{intpart}.{frac}"
        return f"1X2 + {ou} {line}", outcome
    return None


def _decode_team_over_under_with_gg(raw: str) -> tuple[str, str] | None:
    # "Home_Over_2_5_GG" / "Away_Under_1_5_NG" - team over/under + GG/NG combo
    m = re.fullmatch(r"(Home|Away)_(Under|Over)_(\d+)_(\d+)_(GG|NG)", raw)
    if m:
        side, ou, intpart, frac, gg = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        line = f"{intpart}.{frac}"
        gg_label = "BTTS" if gg == "GG" else "No Goals"
        return f"{side} {ou} {line} + {gg_label}", side
    return None


def _decode_btts_short(raw: str) -> tuple[str, str] | None:
    # SportyBet uses "gg" / "ng" as shorthand for Both Teams Score Yes / No
    if raw.lower() == "gg":
        return "Both Teams Score", "Yes"
    if raw.lower() == "ng":
        return "Both Teams Score", "No"
    return None


_RACE_MARKET_TOKENS = frozenset({
    # Bet types where the wonMarket is just the market label (the specific
    # winning combination is implicit in finalOrder, not encoded here)
    "trifecta", "tricast", "forecast", "reverse_forecast", "reverse_tricast",
    "system_two", "system_three", "system_four", "system_five",
    "three_any", "four_any", "five_any", "two_any", "six_any",
    "show_2", "show_3", "show_4", "show_5", "show_6",
    "place_2", "place_3", "place_4", "place_5", "place_6",
    "win", "place", "show",
    "odd", "even",
    "under", "over",  # in races: under/over total runners or finishing positions
    "first_half", "second_half",
})


def _decode_race_market_type(raw: str) -> tuple[str, str] | None:
    """Race-product wonMarkets are often just market type names (the actual
    outcome is implicit in finalOrder). Map those to readable labels."""
    if raw.lower() in _RACE_MARKET_TOKENS:
        return raw.replace("_", " ").title(), "—"
    # Generic system_<*>_<*> family (e.g. "system_four_any", "system_three_all")
    m = re.fullmatch(r"system(?:_[a-z]+)+", raw, re.IGNORECASE)
    if m:
        return raw.replace("_", " ").title(), "—"
    # "show_<id>" / "place_<id>" with a runner id suffix that's not a digit
    m = re.fullmatch(r"(show|place)_([a-zA-Z]\w*)", raw)
    if m:
        return m.group(1).capitalize(), m.group(2)
    return None


def _decode_no_goals(raw: str) -> tuple[str, str] | None:
    # "nog" appears as a wonMarket on 0-0 matches; sportybet shorthand for "No Goal"
    if raw.lower() == "nog":
        return "No Goals", "Yes"
    return None


def _decode_outcome_no_goal_combo(raw: str) -> tuple[str, str] | None:
    # "Draw_No_Goal_MV" / "Home_No_Goal_MV" - 1X2 outcome + no goals (the MV
    # suffix appears to mark the "money-line variant"; we collapse it into the
    # outcome label so the market name is stable)
    m = re.fullmatch(r"(Home|Draw|Away)_No_Goal(?:_MV)?", raw)
    if m:
        return "1X2 + No Goal", m.group(1)
    return None


def _decode_outcome_under_no_goal(raw: str) -> tuple[str, str] | None:
    # "Draw_Under_2_5_NG" - draw + Under <line> + No Goal. Restricted to Draw
    # only because `Home_Under_X_Y_NG` is more naturally read as "Home team
    # scores under X.Y" + No Goals (handled by _decode_team_over_under_with_gg).
    m = re.fullmatch(r"Draw_Under_(\d+)_(\d+)_NG", raw)
    if m:
        line = f"{m.group(1)}.{m.group(2)}"
        return f"1X2 + Under {line} + No Goal", "Draw"
    return None


def _decode_outcome_under_btts(raw: str) -> tuple[str, str] | None:
    # "Draw_Under_2_5_GG" - draw + Under <line> + BTTS
    # "Draw_Over_2_5_GG"  - draw + Over <line> + BTTS
    m = re.fullmatch(r"Draw_(Under|Over)_(\d+)_(\d+)_GG", raw)
    if m:
        line = f"{m.group(2)}.{m.group(3)}"
        return f"1X2 + {m.group(1)} {line} + BTTS", "Draw"
    return None


def _decode_outcome_goal_combo(raw: str) -> tuple[str, str] | None:
    # "Draw_Goal_MV" - draw + at least one goal scored
    m = re.fullmatch(r"(Home|Draw|Away)_Goal(?:_MV)?", raw)
    if m:
        return "1X2 + Goal", m.group(1)
    return None


def _decode_winning_margin_or_more(raw: str) -> tuple[str, str] | None:
    # "Home_By_3_More" / "Away_By_2_More" - winning margin >= N goals
    m = re.fullmatch(r"(Home|Away)_By_(\d+)_More", raw)
    if m:
        return "Winning Margin", f"{m.group(1)} by {m.group(2)} or more"
    return None


def _decode_outcome_with_score(raw: str) -> tuple[str, str] | None:
    # "Draw_0" / "Home_2" - 1X2 outcome with specific total goals count.
    # Catches the leftover "Draw_0" (draw with 0 goals total).
    m = re.fullmatch(r"(Home|Draw|Away)_(\d+)", raw)
    if m:
        return f"1X2 + Total Goals", f"{m.group(1)} ({m.group(2)} goals)"
    return None


# Order matters: most specific first. "Draw_Away" must hit double_chance before
# any odd-only pass treats "Draw" as Match Result. "Draw_0" must NOT hit
# correct_score (no leading underscore) but should hit outcome_with_score.
_PATTERN_DECODERS: list[Callable[[str], tuple[str, str] | None]] = [
    _decode_no_goals,                  # "nog"
    _decode_btts_short,                # "gg" / "ng"
    _decode_correct_score,             # "_2_1"
    _decode_goal_range,                # "_0_3_Goals" - must come before total_goals
    _decode_total_goals,               # "_3_Goals"
    _decode_combo_1x2_under_over,      # "_1x2_Draw_Scores_Under_2_5"
    _decode_outcome_under_no_goal,     # "Draw_Under_2_5_NG" (Draw only)
    _decode_outcome_under_btts,        # "Draw_Over_2_5_GG"
    _decode_outcome_no_goal_combo,     # "Draw_No_Goal_MV"
    _decode_outcome_goal_combo,        # "Draw_Goal_MV"
    _decode_team_over_under_with_gg,   # "Home_Over_2_5_GG"
    _decode_first_half_outcome,        # "_1H_Draw"
    _decode_winning_margin_or_more,    # "Home_By_3_More" - before _decode_team_winning_margin
    _decode_asian_handicap,            # "Home_Plus_0_5"
    _decode_team_winning_margin,       # "Home_By_1"
    _decode_team_over_under,           # "Home_Under_2_5"
    _decode_team_total_goals,          # "Home_0_Goals", "Home_Nil"
    _decode_ht_under,                  # "HT_under075"
    _decode_under_only,                # "under_2_5"
    _decode_over_under,                # "Over_2_5_over", "Over_Under_2_5_over"
    _decode_btts,                      # "BTTS_Yes"
    _decode_double_chance,             # "Home_Draw"
    _decode_ht_ft,                     # "HomeDraw"
    _decode_race_bets,                 # "Win_d3", "Forecast_d3_d1"
    _decode_race_market_type,          # "trifecta", "system_two", "show_3"
    _decode_outcome_with_score,        # "Draw_0", "Home_2" (last - greedy)
]


def _normalize(s: str) -> str:
    """Replace whitespace/dashes with underscores so wire strings and
    schema-market labels can be compared apples-to-apples."""
    return "_".join(s.split())


def preload_markets(session, schema_ids: Iterable[int]) -> dict[int, list[SchemaMarket]]:
    """Fetch all SchemaMarket rows for the given schemas in one query and
    bucket them by schema_id."""
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
