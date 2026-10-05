"""S2-R7 POST /authorizations/{id}/void: payer only, no key, idempotent by state."""
from __future__ import annotations

from datetime import timedelta

import pytest

import pf_model as m
from conftest import check_auth
from pf_client import expect, expect_error


def _hold(w, amount=2_000, frm="ada", to="bob", **extra) -> dict:
    return expect(w.clients[frm].authorize(to, amount, **extra), 201).json()


def test_payer_voids_and_the_hold_is_released(world):
    a = _hold(world)
    v = expect(world.ada.void(a["authorization_id"]), 200).json()
    check_auth(v)
    assert v["authorization_id"] == a["authorization_id"] and v["status"] == "voided"
    assert v["remaining_amount"] == 0 and v["captured_amount"] == 0
    assert v["payment_id"] is None and v["payment_ids"] == []
    for k in ("amount", "from_handle", "to_handle", "note", "visibility", "created_at",
              "expires_at", "currency"):
        assert v[k] == a[k], k
    ada = world.ada.me()
    assert (ada["total"], ada["available"], ada["held"]) == (10_000, 10_000, 0)
    assert world.bob.auth(a["authorization_id"]) == v
    world.oracle()


def test_voiding_twice_is_200_with_the_current_state(world):
    a = _hold(world)
    first = expect(world.ada.void(a["authorization_id"]), 200).json()
    for _ in range(2):
        assert expect(world.ada.void(a["authorization_id"]), 200).json() == first
    world.oracle()


def test_void_needs_no_key_and_ignores_the_body(world):
    a = _hold(world)
    resp = world.ada.request("POST", f"/authorizations/{a['authorization_id']}/void")
    assert expect(resp, 200).json()["status"] == "voided"
    b = _hold(world, amount=5)
    resp = world.ada.post(f"/authorizations/{b['authorization_id']}/void",
                          json={"amount": "junk", "status": "open"})
    assert expect(resp, 200).json()["status"] == "voided"
    world.oracle()


def test_void_after_partial_captures_releases_only_the_remainder(world):
    """[S2-R5] void closes a partially captured hold and preserves the capture records."""
    a = _hold(world)
    aid = a["authorization_id"]
    p1 = expect(world.bob.capture(aid, {"amount": 300, "final": False}), 201).json()
    p2 = expect(world.bob.capture(aid, {"amount": 200, "final": False}), 201).json()
    v = expect(world.ada.void(aid), 200).json()
    assert (v["status"], v["captured_amount"], v["remaining_amount"]) == ("voided", 500, 0)
    assert v["payment_ids"] == [p1["payment_id"], p2["payment_id"]]
    assert v["payment_id"] == p2["payment_id"]
    ada = world.ada.me()
    assert (ada["total"], ada["available"], ada["held"]) == (9_500, 9_500, 0)
    assert world.bob.me()["total"] == 3_000
    expect_error(world.bob.capture(aid, {"amount": 1}), 409, "authorization_not_open")
    world.oracle()


def test_void_of_captured_is_not_open(world):
    a = _hold(world)
    expect(world.bob.capture(a["authorization_id"], {"amount": 1}), 201)
    before = world.oracle()
    expect_error(world.ada.void(a["authorization_id"]), 409, "authorization_not_open")
    assert world.oracle() == before


@pytest.mark.parametrize("status,expected", [("captured", 409), ("expired", 409),
                                             ("voided", 200)])
def test_void_of_seeded_closed_holds(make_world, status, expected):
    w = make_world(m.fixture(authorizations=[m.hold("a_1", "ada", "bob", 100, status=status)]))
    resp = w.ada.void("a_1")
    if expected == 409:
        expect_error(resp, 409, "authorization_not_open")
    else:
        assert expect(resp, 200).json()["status"] == "voided"
    assert w.ada.auth("a_1")["status"] == status
    w.oracle()


def test_void_of_a_clock_expired_hold_is_not_open(make_world):
    past = m.iso(m.now() - timedelta(hours=1, minutes=5))
    w = make_world(m.fixture(authorizations=[m.hold("a_1", "ada", "bob", 100,
                                                    expires_at=past)]))
    expect_error(w.ada.void("a_1"), 409, "authorization_not_open")
    assert w.ada.auth("a_1")["status"] == "expired"


def test_void_permissions(world):
    """[S2-R7, D25] receiver and third party 403; unknown 404."""
    a = _hold(world)
    before = world.oracle()
    expect_error(world.bob.void(a["authorization_id"]), 403, "forbidden")
    expect_error(world.cy.void(a["authorization_id"]), 403, "forbidden")
    expect_error(world.ada.void("a_nope"), 404, "not_found")
    expect_error(world.cy.void("a_nope"), 404, "not_found")
    assert world.oracle() == before
    assert world.ada.auth(a["authorization_id"])["status"] == "open"


def test_void_permission_precedes_state(make_world):
    """[D25] 403 before the captured/expired 409, and before the voided 200."""
    w = make_world(m.fixture(authorizations=[
        m.hold("a_cap", "ada", "bob", 1, status="captured"),
        m.hold("a_exp", "ada", "bob", 1, status="expired"),
        m.hold("a_void", "ada", "bob", 1, status="voided")]))
    for aid in ("a_cap", "a_exp", "a_void"):
        expect_error(w.bob.void(aid), 403, "forbidden")
        expect_error(w.cy.void(aid), 403, "forbidden")


def test_void_needs_a_token(world):
    a = _hold(world)
    expect_error(world.ada.post(f"/authorizations/{a['authorization_id']}/void", json={},
                                token=None), 401, "unauthenticated")


def test_released_funds_are_spendable_at_once(world):
    a = _hold(world, amount=500, frm="cy")
    expect_error(world.cy.pay("bob", 1), 409, "insufficient_funds")
    expect(world.cy.void(a["authorization_id"]), 200)
    expect(world.cy.pay("bob", 500), 201)
    world.oracle()
