from __future__ import annotations

from src.parse.pairing import Pairer


def test_basic_pairing() -> None:
    p = Pairer()
    p.feed_request(1.0, {"xs": 7, "req": {"resource": "/r", "method": "GET"}})
    pair = p.feed_response(1.5, {"xs": 7, "res": {"statusCode": 200, "body": [1]}})
    assert pair is not None
    assert pair.xs == 7
    assert pair.resource == "/r"
    assert pair.method == "GET"
    assert pair.status_code == 200
    assert pair.body == [1]
    assert pair.request_ts == 1.0
    assert pair.response_ts == 1.5
    assert p.pending_count == 0


def test_unpaired_response_returns_none() -> None:
    p = Pairer()
    assert p.feed_response(1.0, {"xs": 7, "res": {"statusCode": 200}}) is None


def test_reset_drops_pending_requests() -> None:
    p = Pairer()
    p.feed_request(1.0, {"xs": 1, "req": {"resource": "/r"}})
    p.reset()
    assert p.pending_count == 0
    assert p.feed_response(1.5, {"xs": 1, "res": {"statusCode": 200}}) is None


def test_response_pops_pending() -> None:
    p = Pairer()
    p.feed_request(1.0, {"xs": 1, "req": {"resource": "/r"}})
    assert p.pending_count == 1
    p.feed_response(1.5, {"xs": 1, "res": {"statusCode": 200}})
    assert p.pending_count == 0


def test_ignores_non_int_xs() -> None:
    p = Pairer()
    p.feed_request(1.0, {"xs": "abc", "req": {"resource": "/r"}})
    assert p.pending_count == 0


def test_ignores_non_dict_res_or_req() -> None:
    p = Pairer()
    p.feed_request(1.0, {"xs": 1, "req": "not a dict"})
    assert p.pending_count == 0
    p.feed_request(1.0, {"xs": 1, "req": {"resource": "/r"}})
    assert p.feed_response(1.5, {"xs": 1, "res": "not a dict"}) is None
