from __future__ import annotations

from src.parse.products.football import FootballProduct
from src.parse.schemas import schema_rows_from_template


FB_SCHEMA_TPL = {
    "id": 41104,
    "description": "England 2026 - OS31",
    "descriptionTag": "ENGLAND",
    "marketTemplateId": 99,
    "filter": {
        "competitionType": "LEAGUE",
        "numParticipants": 20,
        "isTwoLegsGroup": True,
        "libraryId": "lib-1",
    },
    "schedulerConfiguration": [{"dailySchedule": [{"countdown": 180}]}],
    "participantTemplates": [
        {"classType": "FbParticipant", "id": "501", "name": "psg", "fifaCode": "PSG", "stars": 5.0},
        {"classType": "FbParticipant", "id": "336", "name": "liverpool", "fifaCode": "LIV", "stars": 4.5},
    ],
    "marketTemplates": [
        {
            "id": "m1", "name": "Match Result",
            "odds": [
                {"id": "o1", "name": "Home", "value": 0},
                {"id": "o2", "name": "Away", "value": 1},
                {"id": "o3", "name": "Draw", "value": 2},
            ],
        }
    ],
}


def _football_event_block(*, with_data: bool = True, with_result: bool = False) -> dict:
    block: dict = {
        "eBlockId": 12345,
        "playlistId": 41104,
        "serverStatus": "SCHEDULED",
        "eventTime": "2026-05-09T15:42:00Z",
        "data": {
            "classType": "FbEventBlockData",
            "matchDay": 17,
            "weekDay": 6,
            "phase": "GROUPS",
            "legOrder": 2,
            "champId": 9238,
        },
        "events": [{"data": None, "result": None}],
    }
    if with_data:
        block["events"][0]["data"] = {
            "classType": "FbParticipantBlockData",
            "participants": [
                {"classType": "FbParticipant", "id": "501", "stars": 5.0},
                {"classType": "FbParticipant", "id": "336", "stars": 4.5},
            ],
            "oddValues": ["1.85", "4.20", "3.40"],
        }
    if with_result:
        block["events"][0]["result"] = {
            "finalOutcome": ["2", "1"],
            "wonMarkets": ["Match_Result_Home", "_2_1"],
        }
        block["events"][0]["contentDuration"] = 92.5
    return block


def test_schema_template_extracts_schema_rows() -> None:
    schema, markets, parts = schema_rows_from_template(FB_SCHEMA_TPL, "football", 100.0)
    assert schema.schema_id == 41104
    assert schema.product == "football"
    assert schema.kind == "league"
    assert schema.num_participants == 20
    assert schema.is_two_legs_group is True
    assert schema.countdown_s == 180
    assert {(m.slot, m.market_name, m.odd_name) for m in markets} == {
        (0, "Match Result", "Home"),
        (1, "Match Result", "Away"),
        (2, "Match Result", "Draw"),
    }
    assert {(p.team_id, p.fifa_code) for p in parts} == {("501", "PSG"), ("336", "LIV")}


def test_tournament_classified_as_tournament() -> None:
    tpl = {**FB_SCHEMA_TPL, "id": 51000, "filter": {**FB_SCHEMA_TPL["filter"], "competitionType": "CHAMPION"}}
    schema, _, _ = schema_rows_from_template(tpl, "football", 100.0)
    assert schema.kind == "tournament"


def test_football_event_data_extraction() -> None:
    fb = FootballProduct()
    block = _football_event_block(with_data=True, with_result=False)
    events, odds, parts, runners, _ = fb.extract_event_rows(
        block, "/eventBlocks/event/data", 50.0,
    )
    assert len(events) == 1 and len(runners) == 0
    e = events[0]
    assert e.e_block_id == 12345
    assert e.schema_id == 41104
    assert e.product == "football"
    assert e.match_day == 17
    assert e.phase == "GROUPS"
    assert e.home_team_id == "501"
    assert e.away_team_id == "336"
    assert e.captured_ts == 50.0
    assert e.settled_ts is None
    assert e.home_score is None
    assert {p.side: p.team_id for p in parts} == {"home": "501", "away": "336"}
    assert {(o.slot, o.odds) for o in odds} == {(0, 1.85), (1, 4.20), (2, 3.40)}


def test_football_event_result_extraction() -> None:
    fb = FootballProduct()
    block = _football_event_block(with_data=False, with_result=True)
    events, odds, parts, runners, _ = fb.extract_event_rows(
        block, "/eventBlocks/event/result", 75.0,
    )
    assert len(events) == 1 and len(odds) == 0 and len(parts) == 0
    e = events[0]
    assert e.home_score == 2
    assert e.away_score == 1
    assert e.won_markets == "Match_Result_Home,_2_1"
    assert e.won_market_count == 2
    assert e.settled_ts == 75.0
    assert e.content_duration == 92.5


def test_football_standings_extraction() -> None:
    fb = FootballProduct()
    stats_block = {
        "eBlockId": 999,
        "playlistId": 41104,
        "data": {"classType": "FbEventBlockData", "matchDay": 12},
        "stats": {
            "classType": "FbEventBlockStats",
            "groupClassification": [{
                "entries": [
                    {"participantId": "501", "ranking": 1, "points": 30, "wins": 9, "draws": 3, "losses": 0,
                     "goalsFor": 25, "goalsAgainst": 7, "goalDiff": 18, "history": ["W", "W", "D"]},
                    {"participantId": "336", "ranking": 2, "points": 26, "wins": 8, "draws": 2, "losses": 2},
                ]
            }]
        }
    }
    standings = fb.extract_stats_rows(stats_block, captured_ts=80.0)
    assert len(standings) == 2
    by_team = {s.team_id: s for s in standings}
    assert by_team["501"].points == 30
    assert by_team["501"].history == "W,W,D"
    assert by_team["336"].ranking == 2
    assert by_team["501"].match_day == 12
