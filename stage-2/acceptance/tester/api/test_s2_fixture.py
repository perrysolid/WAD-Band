"""S2-R2 / D33 the stage-2 fixture: ttl, seeded authorizations, derived available,
and every reset error leaving the previous state untouched."""
from __future__ import annotations

from datetime import timedelta

import pytest

import pf_model as m
from conftest import World, check_auth
from pf_client import expect, expect_error


def _later(hours=2):
    return m.iso(m.now() + timedelta(hours=hours))


def _earlier(hours=2):
    return m.iso(m.now() - timedelta(hours=hours))


def test_stage1_shaped_fixture_works_unchanged(make_world):
    """[S2-R2] no ttl, no authorizations key: empty list, ttl 600."""
    fx = m.fixture()
    assert "authorizations" not in fx and "authorization_ttl_seconds" not in fx
    w = make_world(fx)
    assert w.ada.auths() == [] and w.ada.me()["held"] == 0
    a = expect(w.ada.authorize("bob", 1), 201).json()
    assert m.parse(a["expires_at"]) - m.parse(a["created_at"]) == timedelta(seconds=600)
    w.oracle()


def test_empty_authorizations_list(make_world):
    w = make_world(m.fixture(authorizations=[]))
    assert w.ada.auths() == []


@pytest.mark.parametrize("ttl,seconds", [(600.0, 600), (1e3, 1_000), (1, 1),
                                         (1_000_000_000, 1_000_000_000)])
def test_integral_ttl_forms_are_accepted(make_world, ttl, seconds):
    """[S2-R2, D3] a positive integer by numeric value."""
    w = make_world(m.fixture(ttl=ttl))
    a = expect(w.ada.authorize("bob", 1), 201).json()
    assert m.parse(a["expires_at"]) - m.parse(a["created_at"]) == timedelta(seconds=seconds)
    m.assert_d35(a["expires_at"])


@pytest.fixture
def previous(make_world):
    """A distinctive state that a refused reset must leave intact."""
    w = make_world(m.fixture(ttl=7, authorizations=[m.hold("a_prev", "ada", "bob", 1_234)]))
    expect(w.ada.pay("cy", 66), 201)
    return w


def _assert_previous_intact(w: World):
    ada = w.ada.me()
    assert (ada["total"], ada["held"], ada["available"]) == (9_934, 1_234, 8_700)
    assert [a["authorization_id"] for a in w.ada.auths()] == ["a_prev"]
    assert len(w.ada.feed()) == 1
    a = expect(w.ada.authorize("bob", 1), 201).json()
    assert m.parse(a["expires_at"]) - m.parse(a["created_at"]) == timedelta(seconds=7), \
        "the previous ttl must survive a refused reset"


@pytest.mark.parametrize("ttl", [0, -1, 1.5, "600", True, None, [600], {"s": 1}, -0.0, 1e-3])
def test_invalid_ttl_is_422_and_changes_nothing(previous, reset, ttl):
    resp = reset(m.fixture(ttl=ttl), raw=True)
    expect_error(resp, 422, "validation_failed")
    _assert_previous_intact(previous)


def _bad_holds():
    ok = dict(m.hold("a_1", "ada", "bob", 100))
    cases = {
        "unknown-from": {**ok, "from_user_id": "u_nobody"},
        "unknown-to": {**ok, "to_user_id": "u_nobody"},
        "bad-status": {**ok, "status": "pending"},
        "bad-status-case": {**ok, "status": "OPEN"},
        "zero-amount": {**ok, "amount": 0},
        "string-amount": {**ok, "amount": "100"},
        "fraction-amount": {**ok, "amount": 1.5},
        "huge-amount": {**ok, "amount": 1_000_000_001},
        "bad-visibility": {**ok, "visibility": "secret"},
        "long-note": {**ok, "note": "x" * 201},
        "bad-expires": {**ok, "expires_at": "tomorrow"},
        "no-offset": {**ok, "expires_at": _later().replace("+00:00", "")},
        "bad-date": {**ok, "expires_at": "2026-02-30T10:00:00+00:00"},
        "missing-expires": {k: v for k, v in ok.items() if k != "expires_at"},
        "missing-id": {k: v for k, v in ok.items() if k != "id"},
        "missing-amount": {k: v for k, v in ok.items() if k != "amount"},
        "not-object": "a_1",
    }
    return cases


BAD = _bad_holds()


@pytest.mark.parametrize("name", list(BAD))
def test_invalid_seeded_authorization_is_422_and_changes_nothing(previous, reset, name):
    resp = reset(m.fixture(authorizations=[BAD[name]]), raw=True)
    expect_error(resp, 422, "validation_failed")
    _assert_previous_intact(previous)


@pytest.mark.parametrize("value", ["nope", {"a": 1}, 5])
def test_authorizations_must_be_an_array(previous, reset, value):
    fx = m.fixture()
    fx["authorizations"] = value
    expect_error(reset(fx, raw=True), 422, "validation_failed")
    _assert_previous_intact(previous)


def test_duplicate_authorization_ids_are_422(previous, reset):
    fx = m.fixture(authorizations=[m.hold("a_1", "ada", "bob", 1), m.hold("a_1", "bob", "ada", 1)])
    expect_error(reset(fx, raw=True), 422, "validation_failed")
    _assert_previous_intact(previous)


def test_unexpired_open_holds_above_balance_are_a_reset_error(previous, reset):
    """[S2 Model] Σ seeded unexpired open holds > balance -> 422, nothing changes."""
    fx = m.fixture(authorizations=[m.hold("a_1", "cy", "bob", 300),
                                   m.hold("a_2", "cy", "ada", 201)])
    expect_error(reset(fx, raw=True), 422, "validation_failed")
    _assert_previous_intact(previous)


def test_holds_exactly_equal_to_balance_are_fine(make_world):
    w = make_world(m.fixture(authorizations=[m.hold("a_1", "cy", "bob", 300),
                                             m.hold("a_2", "cy", "ada", 200)]))
    cy = w.cy.me()
    assert (cy["total"], cy["available"], cy["held"]) == (500, 0, 500)
    expect_error(w.cy.pay("bob", 1), 409, "insufficient_funds")
    w.oracle()


@pytest.mark.parametrize("status", ["captured", "voided", "expired"])
def test_closed_seeded_holds_do_not_count_toward_the_limit(make_world, status):
    w = make_world(m.fixture(authorizations=[m.hold("a_1", "cy", "bob", 9_999, status=status)]))
    cy = w.cy.me()
    assert (cy["total"], cy["available"], cy["held"]) == (500, 500, 0)
    w.oracle()


def test_past_open_holds_do_not_count_toward_the_limit(make_world):
    w = make_world(m.fixture(authorizations=[
        m.hold("a_1", "cy", "bob", 9_999, expires_at=_earlier(1.5))]))
    cy = w.cy.me()
    assert (cy["total"], cy["available"], cy["held"]) == (500, 500, 0)
    assert w.cy.auth("a_1")["status"] == "expired"
    w.oracle()


def test_available_is_derived_never_seeded(make_world):
    """[S2 Model] `available`/`held`/`total` on a fixture user are ignored."""
    users = [dict(m.ADA, available=1, held=999, total=5), m.BOB, m.CY]
    w = make_world(m.fixture(users, authorizations=[m.hold("a_1", "ada", "bob", 2_000)]))
    ada = w.ada.me()
    assert (ada["balance"], ada["total"], ada["available"], ada["held"]) == \
        (10_000, 10_000, 8_000, 2_000)


def test_seeded_holds_are_visible_immediately_after_reset(make_world):
    """[S2-R2, D33] all fields, statuses as seeded, D33 defaults."""
    exp = _later(3)
    w = make_world(m.fixture(authorizations=[
        m.hold("a_open", "ada", "bob", 2_000, note="deposit", visibility="private",
               expires_at=exp),
        m.hold("a_cap", "ada", "bob", 700, status="captured"),
        m.hold("a_void", "bob", "ada", 300, status="voided"),
        m.hold("a_exp", "cy", "ada", 50, status="expired")]))
    got = {a["authorization_id"]: a for a in w.ada.auths()}
    assert set(got) == {"a_open", "a_cap", "a_void", "a_exp"}
    o = got["a_open"]
    for a in got.values():
        check_auth(a, seeded=True)
    assert (o["status"], o["amount"], o["captured_amount"], o["remaining_amount"]) == \
        ("open", 2_000, 0, 2_000)
    assert (o["note"], o["visibility"], o["payment_id"]) == ("deposit", "private", None)
    assert (o["from_handle"], o["to_handle"], o["from_user_id"], o["to_user_id"]) == \
        ("ada", "bob", "u_ada", "u_bob")
    assert o["currency"] == "EUR"
    assert m.parse(o["expires_at"]) == m.parse(exp)
    assert got["a_cap"]["captured_amount"] == 700, "D33: captured defaults to amount"
    assert got["a_void"]["captured_amount"] == 0 and got["a_exp"]["status"] == "expired"
    assert abs((m.parse(o["created_at"]) - m.now()).total_seconds()) < 60, \
        "D33: seeded created_at is the reset instant"
    ada = w.ada.me()
    assert (ada["total"], ada["available"], ada["held"]) == (10_000, 8_000, 2_000)
    assert w.bob.me()["available"] == 2_500
    w.oracle()


def test_seeded_partially_captured_open_hold(make_world):
    """[D33] an explicit captured_amount on an open hold reserves only the remainder."""
    w = make_world(m.fixture(authorizations=[
        m.hold("a_1", "ada", "bob", 2_000, captured_amount=500)]))
    a = w.ada.auth("a_1")
    assert (a["captured_amount"], a["remaining_amount"]) == (500, 1_500)
    assert w.ada.me()["held"] == 1_500
    expect_error(w.bob.capture("a_1", {"amount": 1_501}), 422, "capture_exceeds_authorization")
    expect(w.bob.capture("a_1", {"amount": 1_500}), 201)
    assert w.ada.me()["total"] == 8_500
    w.oracle(feeds=False)


def test_reset_replaces_holds(make_world, reset):
    w = make_world(m.fixture(authorizations=[m.hold("a_1", "ada", "bob", 2_000)]))
    expect(w.ada.authorize("bob", 5), 201)
    reset(m.fixture())
    assert w.ada.auths() == [] and w.ada.me()["held"] == 0


def test_seeded_hold_with_unknown_fields_is_fine(make_world):
    w = make_world(m.fixture(authorizations=[
        m.hold("a_1", "ada", "bob", 10, zzz=[1], closed_at="x")]))
    assert w.ada.auth("a_1")["status"] == "open"
