from __future__ import annotations

from src.parse.products.dogs import DogsProduct


def _dogs_block(*, with_data: bool = True, with_result: bool = False) -> dict:
    block: dict = {
        "eBlockId": 88888,
        "playlistId": 71001,
        "serverStatus": "SCHEDULED",
        "eventTime": "2026-05-09T15:30:00Z",
        "events": [{"data": None, "result": None}],
    }
    if with_data:
        block["events"][0]["data"] = {
            "participants": [
                {"classType": "DogParticipant", "id": "d1", "name": "Lightning", "prob": 0.20, "form": 3.0,
                 "ability": 70.0, "speed": 65.0, "stamina": 80.0, "wins": 5.0, "place": 7.0, "pace": "F"},
                {"classType": "DogParticipant", "id": "d2", "name": "Thunder", "prob": 0.15, "form": 2.5},
                {"classType": "DogParticipant", "id": "d3", "name": "Bolt"},
            ],
            "gameData": {
                "trackCondition": 0.85,
                "weather": "clear",
                "surface": "sand",
                "distance": 480.0,
            },
            "oddValues": ["3.50", "5.20", "12.00"],
        }
    if with_result:
        block["events"][0]["result"] = {
            "data": {"finalOrder": ["d2", "d1", "d3"], "mediaId": "med-9"},
            "wonMarkets": ["Win_d2", "Place_d1"],
        }
        block["events"][0]["contentDuration"] = 31.5
    return block


def test_dogs_event_data_extracts_runners_and_odds() -> None:
    dogs = DogsProduct()
    events, odds, fb_parts, runners, _ = dogs.extract_event_rows(
        _dogs_block(with_data=True), "/eventBlocks/event/data", 100.0,
    )
    assert len(fb_parts) == 0
    e = events[0]
    assert e.e_block_id == 88888
    assert e.product == "dogs"
    assert e.num_runners == 3
    assert e.distance == 480.0
    assert e.surface == "sand"
    assert e.weather == "clear"
    assert e.track_condition == 0.85
    assert {(o.slot, o.odds) for o in odds} == {(0, 3.50), (1, 5.20), (2, 12.00)}
    assert [r.runner_id for r in runners] == ["d1", "d2", "d3"]
    assert [r.trap for r in runners] == [1, 2, 3]
    assert runners[0].name == "Lightning"
    assert runners[0].prob == 0.20


def test_dogs_event_result_extracts_finishing_order() -> None:
    dogs = DogsProduct()
    events, _, _, _, _ = dogs.extract_event_rows(
        _dogs_block(with_data=False, with_result=True), "/eventBlocks/event/result", 200.0,
    )
    e = events[0]
    assert e.winner_id == "d2"
    assert e.second_id == "d1"
    assert e.third_id == "d3"
    assert e.final_order == "d2,d1,d3"
    assert e.media_id == "med-9"
    assert e.won_market_count == 2
    assert e.content_duration == 31.5
    assert e.settled_ts == 200.0
