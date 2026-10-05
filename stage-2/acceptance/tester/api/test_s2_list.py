"""S2-R8 GET /authorizations: ownership, newest first, filters, pagination."""
from __future__ import annotations

from datetime import timedelta

import pytest

import pf_model as m
from pf_client import expect, expect_error


@pytest.fixture
def lw(make_world):
    """Ada 10000, Bob 2500, Cy 500, Dee 1000; seeded holds in every status."""
    past = m.iso(m.now() - timedelta(hours=2))
    return make_world(m.fixture(
        [m.ADA, m.BOB, m.CY, m.user("dee", 1_000)],
        authorizations=[m.hold("a_s_open", "dee", "ada", 100),
                        m.hold("a_s_cap", "ada", "dee", 100, status="captured"),
                        m.hold("a_s_void", "bob", "ada", 100, status="voided"),
                        m.hold("a_s_exp", "ada", "bob", 100, status="expired"),
                        m.hold("a_s_clock", "cy", "ada", 100, expires_at=past)]))


def _ids(items):
    return [a["authorization_id"] for a in items]


def test_response_shape(world):
    body = expect(world.ada.get("/authorizations"), 200).json()
    assert body == {"authorizations": [], "has_more": False}


def test_only_authorizations_involving_the_caller(lw):
    mine = expect(lw.ada.authorize("bob", 5), 201).json()["authorization_id"]
    theirs = expect(lw.bob.authorize("cy", 5, visibility="public"), 201).json()["authorization_id"]
    ada_ids = set(_ids(lw.ada.auths()))
    assert mine in ada_ids and theirs not in ada_ids
    assert ada_ids == {mine, "a_s_open", "a_s_cap", "a_s_void", "a_s_exp", "a_s_clock"}
    assert set(_ids(lw.cy.auths())) == {theirs, "a_s_clock"}
    assert set(_ids(lw.dee.auths())) == {"a_s_open", "a_s_cap"}
    lw.oracle()


def test_newest_first(world):
    made = [expect(world.ada.authorize(h, 1 + i), 201).json()["authorization_id"]
            for i, h in enumerate(["bob", "cy", "bob", "cy", "bob"])]
    listed = world.ada.auths()
    assert _ids(listed) == list(reversed(made))
    stamps = [m.parse(a["created_at"]) for a in listed]
    assert stamps == sorted(stamps, reverse=True)
    assert _ids(world.bob.auths()) == [made[4], made[2], made[0]]


def test_direction_filter(lw):
    out = set(_ids(lw.ada.auths(direction="outgoing")))
    inc = set(_ids(lw.ada.auths(direction="incoming")))
    assert out == {"a_s_cap", "a_s_exp"}
    assert inc == {"a_s_open", "a_s_void", "a_s_clock"}
    for a in lw.ada.auths(direction="outgoing"):
        assert a["from_handle"] == "ada"
    for a in lw.ada.auths(direction="incoming"):
        assert a["to_handle"] == "ada"


@pytest.mark.parametrize("status,expected", [
    ("open", {"a_s_open"}), ("captured", {"a_s_cap"}), ("voided", {"a_s_void"}),
    ("expired", {"a_s_exp", "a_s_clock"})])
def test_status_filter_and_clock_expiry(lw, status, expected):
    """[S2-R8] a clock-expired hold matches expired, never open."""
    got = lw.ada.auths(status=status)
    assert set(_ids(got)) == expected
    assert all(a["status"] == status for a in got)


def test_status_and_direction_combine(lw):
    assert _ids(lw.ada.auths(status="expired", direction="incoming")) == ["a_s_clock"]
    assert _ids(lw.ada.auths(status="expired", direction="outgoing")) == ["a_s_exp"]
    assert lw.ada.auths(status="open", direction="outgoing") == []


def test_status_follows_lifecycle(world):
    a = expect(world.ada.authorize("bob", 100), 201).json()["authorization_id"]
    b = expect(world.ada.authorize("bob", 100), 201).json()["authorization_id"]
    c = expect(world.ada.authorize("bob", 100), 201).json()["authorization_id"]
    expect(world.bob.capture(a, {"amount": 50, "final": False}), 201)
    expect(world.bob.capture(b), 201)
    expect(world.ada.void(c), 200)
    assert _ids(world.bob.auths(status="open")) == [a]
    assert _ids(world.bob.auths(status="captured")) == [b]
    assert _ids(world.bob.auths(status="voided")) == [c]
    world.oracle()


@pytest.mark.parametrize("params", [
    {"direction": "both"}, {"direction": "OUTGOING"}, {"direction": ""},
    {"status": "pending"}, {"status": "Open"}, {"status": ""}, {"status": "closed"},
    {"limit": "0"}, {"limit": "201"}, {"limit": "-1"}, {"limit": "1e1"}, {"limit": "4.0"},
    {"limit": "+4"}, {"limit": "abc"}, {"limit": ""}, {"limit": " 4"},
    {"offset": "-1"}, {"offset": "1e9"}, {"offset": "x"}, {"offset": ""},
])
def test_bad_query_values_are_422(world, params):
    expect_error(world.ada.get("/authorizations", params=params), 422, "validation_failed")


def test_pagination_limits_and_has_more(world):
    made = [expect(world.ada.authorize("bob", 1), 201).json()["authorization_id"]
            for _ in range(5)]
    newest = list(reversed(made))
    p1 = expect(world.ada.get("/authorizations", params={"limit": "2"}), 200).json()
    assert _ids(p1["authorizations"]) == newest[:2] and p1["has_more"] is True
    p3 = expect(world.ada.get("/authorizations", params={"limit": "2", "offset": "4"}),
                200).json()
    assert _ids(p3["authorizations"]) == newest[4:] and p3["has_more"] is False
    exact = expect(world.ada.get("/authorizations", params={"limit": "5"}), 200).json()
    assert len(exact["authorizations"]) == 5 and exact["has_more"] is False
    far = expect(world.ada.get("/authorizations", params={"offset": "1000"}), 200).json()
    assert far == {"authorizations": [], "has_more": False}
    lead = expect(world.ada.get("/authorizations", params={"limit": "002"}), 200).json()
    assert len(lead["authorizations"]) == 2
    big = expect(world.ada.get("/authorizations", params={"limit": "200"}), 200).json()
    assert len(big["authorizations"]) == 5


def test_default_limit_is_50(make_world):
    w = make_world(m.fixture([m.user("ada", 100), m.BOB]))
    for _ in range(51):
        expect(w.ada.authorize("bob", 1), 201)
    body = expect(w.ada.get("/authorizations"), 200).json()
    assert len(body["authorizations"]) == 50 and body["has_more"] is True
    assert len(w.ada.auths()) == 51
    assert w.ada.me()["held"] == 51
    w.oracle()


def test_listing_needs_a_token(world):
    expect_error(world.ada.get("/authorizations", token=None), 401, "unauthenticated")


def test_unknown_query_parameters_are_ignored(world):
    expect(world.ada.authorize("bob", 1), 201)
    body = expect(world.ada.get("/authorizations", params={"as_of": "x", "zzz": "1"}),
                  200).json()
    assert len(body["authorizations"]) == 1
