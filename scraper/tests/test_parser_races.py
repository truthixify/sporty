from __future__ import annotations

import pytest

from src.parse.products import (
    DogsProduct,
    HorsesProduct,
    MotorbikesProduct,
    SpeedwayProduct,
    detect_for_event_block,
    detect_for_schema_template,
)


def _race_block(participant_class: str, *, with_result: bool = False) -> dict:
    block = {
        "eBlockId": 7777, "playlistId": 71001,
        "serverStatus": "FINISHED" if with_result else "SCHEDULED",
        "eventTime": "2026-05-09T16:00:00Z",
        "events": [{
            "data": {
                "participants": [
                    {"classType": participant_class, "id": "r1", "name": "Alpha", "prob": 0.4},
                    {"classType": participant_class, "id": "r2", "name": "Beta", "prob": 0.3},
                    {"classType": participant_class, "id": "r3", "name": "Gamma"},
                ],
                "gameData": {"distance": 480.0, "surface": "sand"},
                "oddValues": ["2.10", "3.30", "8.00"],
            }
        }],
    }
    if with_result:
        block["events"][0]["result"] = {
            "data": {"finalOrder": ["r2", "r3", "r1"]},
            "wonMarkets": ["Win_r2"],
        }
    return block


def _race_schema_template(participant_class: str) -> dict:
    return {
        "id": 71001,
        "description": "Test Race",
        "filter": {"competitionType": "RACE", "numParticipants": 3},
        "schedulerConfiguration": [{"dailySchedule": [{"countdown": 90}]}],
        "participantTemplates": [
            {"classType": participant_class, "id": "r1", "name": "Alpha"},
        ],
        "marketTemplates": [],
    }


@pytest.mark.parametrize(
    ("product_cls", "participant_class"),
    [
        (DogsProduct, "DogParticipant"),
        (HorsesProduct, "HorseParticipant"),
        (SpeedwayProduct, "SpeedwayParticipant"),
        (MotorbikesProduct, "MotorbikeParticipant"),
        (MotorbikesProduct, "MotorbikesParticipant"),
    ],
)
def test_event_block_dispatched_to_correct_product(product_cls, participant_class) -> None:
    block = _race_block(participant_class)
    p = detect_for_event_block(block)
    assert p is not None
    assert isinstance(p, product_cls)


@pytest.mark.parametrize(
    ("product_cls", "participant_class"),
    [
        (DogsProduct, "DogParticipant"),
        (HorsesProduct, "HorseParticipant"),
        (SpeedwayProduct, "SpeedwayParticipant"),
        (MotorbikesProduct, "MotorbikeParticipant"),
    ],
)
def test_schema_dispatched_to_correct_product(product_cls, participant_class) -> None:
    tpl = _race_schema_template(participant_class)
    p = detect_for_schema_template(tpl)
    assert p is not None
    assert isinstance(p, product_cls)


@pytest.mark.parametrize(
    ("product", "participant_class"),
    [
        (HorsesProduct(), "HorseParticipant"),
        (SpeedwayProduct(), "SpeedwayParticipant"),
        (MotorbikesProduct(), "MotorbikeParticipant"),
    ],
)
def test_race_event_extraction_writes_runners_and_odds(product, participant_class) -> None:
    block = _race_block(participant_class)
    events, odds, fb_parts, runners, _ = product.extract_event_rows(
        block, "/eventBlocks/event/data", 100.0,
    )
    assert len(events) == 1
    assert events[0].product == product.name
    assert events[0].num_runners == 3
    assert events[0].distance == 480.0
    assert {(o.slot, o.odds) for o in odds} == {(0, 2.10), (1, 3.30), (2, 8.00)}
    assert [r.runner_id for r in runners] == ["r1", "r2", "r3"]
    assert [r.trap for r in runners] == [1, 2, 3]
    assert runners[0].name == "Alpha"
    assert fb_parts == []


@pytest.mark.parametrize(
    ("product", "participant_class"),
    [
        (HorsesProduct(), "HorseParticipant"),
        (SpeedwayProduct(), "SpeedwayParticipant"),
        (MotorbikesProduct(), "MotorbikeParticipant"),
    ],
)
def test_race_result_extracts_finishing_order(product, participant_class) -> None:
    block = _race_block(participant_class, with_result=True)
    events, _, _, _, _ = product.extract_event_rows(
        block, "/eventBlocks/event/result", 200.0,
    )
    e = events[0]
    assert e.winner_id == "r2"
    assert e.second_id == "r3"
    assert e.third_id == "r1"
    assert e.final_order == "r2,r3,r1"
    assert e.settled_ts == 200.0


def test_motorbikes_uses_rider_field_when_name_missing() -> None:
    block = _race_block("MotorbikeParticipant")
    block["events"][0]["data"]["participants"] = [
        {"classType": "MotorbikeParticipant", "id": "m1", "rider": "Rossi", "bike": "Ducati"},
    ]
    block["events"][0]["data"]["oddValues"] = ["2.0"]
    p = MotorbikesProduct()
    events, _, _, runners, _ = p.extract_event_rows(block, "/eventBlocks/event/data", 1.0)
    assert runners[0].name == "Rossi"
    assert runners[0].raw["bike"] == "Ducati"
