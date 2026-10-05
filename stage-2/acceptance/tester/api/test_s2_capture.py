"""S2-R4/R5/R6 POST /authorizations/{id}/capture: payment shape, final and extended
(partial) capture, release of the remainder, every error and the D25 precedence."""
from __future__ import annotations

from datetime import timedelta

import pytest

import pf_model as m
from pf_client import expect, expect_error, expect_one_of, new_key

PAYMENT_FIELDS = {"payment_id", "from_user_id", "from_handle", "to_user_id", "to_handle",
                  "amount", "currency", "note", "visibility", "request_id", "created_at",
                  "authorization_id", "settlement_id"}


def _hold(w, amount=2_000, frm="ada", to="bob", **extra) -> dict:
    return expect(w.clients[frm].authorize(to, amount, **extra), 201).json()


def test_full_capture_returns_a_payment(world):
    """[S2-R4] 201 payment in the POST /payments shape, linked to the authorization."""
    a = _hold(world, note="deposit", visibility="private")
    p = expect(world.bob.capture(a["authorization_id"], {}), 201).json()
    assert PAYMENT_FIELDS <= set(p), PAYMENT_FIELDS - set(p)
    assert p["authorization_id"] == a["authorization_id"]
    assert p["request_id"] is None and p["settlement_id"] is None
    assert p["amount"] == 2_000 and p["currency"] == "EUR"
    assert (p["from_user_id"], p["from_handle"], p["to_user_id"], p["to_handle"]) == \
        ("u_ada", "ada", "u_bob", "bob")
    assert p["note"] == "deposit" and p["visibility"] == "private"
    m.assert_d35(p["created_at"])
    after = world.bob.auth(a["authorization_id"])
    assert after["status"] == "captured" and after["captured_amount"] == 2_000
    assert after["remaining_amount"] == 0
    assert after["payment_id"] == p["payment_id"] and after["payment_ids"] == [p["payment_id"]]
    ada = world.ada.me()
    assert (ada["balance"], ada["total"], ada["available"], ada["held"]) == \
        (8_000, 8_000, 8_000, 0)
    assert world.bob.me()["total"] == 4_500
    world.oracle()


def test_partial_final_capture_releases_the_remainder_in_the_same_step(world):
    """[S2-R5] capturing 1500 of 2000 returns 500 to the payer's available at once."""
    a = _hold(world)
    p = expect(world.bob.capture(a["authorization_id"], {"amount": 1_500}), 201).json()
    assert p["amount"] == 1_500
    ada = world.ada.me()
    assert (ada["total"], ada["available"], ada["held"]) == (8_500, 8_500, 0)
    after = world.ada.auth(a["authorization_id"])
    assert (after["status"], after["captured_amount"], after["remaining_amount"]) == \
        ("captured", 1_500, 0)
    world.oracle()


def test_explicit_final_true_is_the_default_behaviour(world):
    a = _hold(world)
    expect(world.bob.capture(a["authorization_id"], {"amount": 1, "final": True}), 201)
    after = world.ada.auth(a["authorization_id"])
    assert after["status"] == "captured" and world.ada.me()["held"] == 0
    expect_error(world.bob.capture(a["authorization_id"], {"amount": 1}), 409,
                 "authorization_not_open")
    world.oracle()


def test_second_capture_after_final_is_not_open(world):
    """[S2-R5] default: one final capture per authorization."""
    a = _hold(world)
    expect(world.bob.capture(a["authorization_id"], {"amount": 500}), 201)
    before = world.oracle()
    for body in ({}, {"amount": 1}, {"amount": 1, "final": False}, {"amount": 1_500}):
        expect_error(world.bob.capture(a["authorization_id"], body), 409,
                     "authorization_not_open")
    assert world.oracle() == before


def test_extended_capture_keeps_the_remainder_held(world):
    """[S2-R5] final:false keeps status open; captured cumulative; payment_ids in order."""
    a = _hold(world)
    aid = a["authorization_id"]
    p1 = expect(world.bob.capture(aid, {"amount": 700, "final": False}), 201).json()
    mid = world.ada.auth(aid)
    assert (mid["status"], mid["captured_amount"], mid["remaining_amount"]) == ("open", 700, 1_300)
    assert mid["payment_id"] == p1["payment_id"] and mid["payment_ids"] == [p1["payment_id"]]
    ada = world.ada.me()
    assert (ada["total"], ada["available"], ada["held"]) == (9_300, 8_000, 1_300)
    p2 = expect(world.bob.capture(aid, {"amount": 300, "final": False}), 201).json()
    mid = world.ada.auth(aid)
    assert (mid["captured_amount"], mid["remaining_amount"]) == (1_000, 1_000)
    assert mid["payment_ids"] == [p1["payment_id"], p2["payment_id"]]
    assert mid["payment_id"] == p2["payment_id"]
    # the omitted amount defaults to the remainder, final by default
    p3 = expect(world.bob.capture(aid, {}), 201).json()
    assert p3["amount"] == 1_000
    done = world.ada.auth(aid)
    assert (done["status"], done["captured_amount"], done["remaining_amount"]) == \
        ("captured", 2_000, 0)
    assert done["payment_ids"] == [p1["payment_id"], p2["payment_id"], p3["payment_id"]]
    assert world.ada.me()["held"] == 0 and world.ada.me()["total"] == 8_000
    assert {p["payment_id"] for p in world.bob.feed()} == set(done["payment_ids"])
    world.oracle()


def test_capturing_the_entire_remainder_closes_it_even_with_final_false(world):
    a = _hold(world)
    aid = a["authorization_id"]
    expect(world.bob.capture(aid, {"amount": 500, "final": False}), 201)
    expect(world.bob.capture(aid, {"amount": 1_500, "final": False}), 201)
    after = world.ada.auth(aid)
    assert (after["status"], after["remaining_amount"]) == ("captured", 0)
    expect_error(world.bob.capture(aid, {"amount": 1, "final": False}), 409,
                 "authorization_not_open")
    world.oracle()


def test_final_false_with_omitted_amount_captures_everything(world):
    a = _hold(world)
    p = expect(world.bob.capture(a["authorization_id"], {"final": False}), 201).json()
    assert p["amount"] == 2_000
    assert world.ada.auth(a["authorization_id"])["status"] == "captured"
    world.oracle()


def test_final_capture_after_partials_releases_only_the_rest(world):
    a = _hold(world)
    aid = a["authorization_id"]
    expect(world.bob.capture(aid, {"amount": 400, "final": False}), 201)
    expect(world.bob.capture(aid, {"amount": 100, "final": True}), 201)
    after = world.ada.auth(aid)
    assert (after["status"], after["captured_amount"], after["remaining_amount"]) == \
        ("captured", 500, 0)
    ada = world.ada.me()
    assert (ada["total"], ada["available"], ada["held"]) == (9_500, 9_500, 0)
    world.oracle()


def test_exceeds_compares_with_the_remaining_amount(world):
    """[S2-R5/R6] 422 capture_exceeds_authorization against the remainder."""
    a = _hold(world)
    aid = a["authorization_id"]
    expect_error(world.bob.capture(aid, {"amount": 2_001}), 422,
                 "capture_exceeds_authorization")
    expect(world.bob.capture(aid, {"amount": 1_200, "final": False}), 201)
    before = world.oracle()
    expect_error(world.bob.capture(aid, {"amount": 801}), 422, "capture_exceeds_authorization")
    expect_error(world.bob.capture(aid, {"amount": 2_000, "final": False}), 422,
                 "capture_exceeds_authorization")
    assert world.oracle() == before
    expect(world.bob.capture(aid, {"amount": 800}), 201)
    world.oracle()


def test_capture_may_spend_the_reserved_money(world):
    """[S2 §2] captures may spend the money reserved for them, even at available 0."""
    a = _hold(world, amount=500, frm="cy")
    assert world.cy.me()["available"] == 0
    expect(world.bob.capture(a["authorization_id"], {"amount": 200, "final": False}), 201)
    expect(world.bob.capture(a["authorization_id"], {}), 201)
    cy = world.cy.me()
    assert (cy["total"], cy["available"], cy["held"]) == (0, 0, 0)
    world.oracle()


def test_capture_follows_the_feed_visibility_rule(world):
    """[S2-R4] the capture payment appears in /activity by the ordinary rule."""
    pub = _hold(world, amount=10, visibility="public")
    prv = _hold(world, amount=20, visibility="private")
    p_pub = expect(world.bob.capture(pub["authorization_id"]), 201).json()
    p_prv = expect(world.bob.capture(prv["authorization_id"]), 201).json()
    assert p_prv["visibility"] == "private"
    for who, expected in (("ada", {p_pub["payment_id"], p_prv["payment_id"]}),
                          ("bob", {p_pub["payment_id"], p_prv["payment_id"]}),
                          ("cy", {p_pub["payment_id"]})):
        assert {p["payment_id"] for p in world.clients[who].feed()} == expected, who
    feed = {p["payment_id"]: p for p in world.ada.feed()}
    assert feed[p_pub["payment_id"]] == p_pub
    world.oracle()


def test_capture_of_seeded_open_hold(make_world):
    """[S2-R2, S2-R4] a seeded open hold is capturable by its receiver."""
    w = make_world(m.fixture(authorizations=[
        m.hold("a_1", "ada", "bob", 3_000, note="seed", visibility="private")]))
    assert w.ada.me()["available"] == 7_000
    p = expect(w.bob.capture("a_1", {"amount": 1_000}), 201).json()
    assert p["authorization_id"] == "a_1" and p["note"] == "seed"
    assert p["visibility"] == "private"
    a = w.bob.auth("a_1")
    assert (a["status"], a["captured_amount"], a["payment_id"]) == \
        ("captured", 1_000, p["payment_id"])
    ada = w.ada.me()
    assert (ada["total"], ada["available"], ada["held"]) == (9_000, 9_000, 0)
    w.oracle()


@pytest.mark.parametrize("status,code", [("captured", "authorization_not_open"),
                                         ("voided", "authorization_not_open"),
                                         ("expired", "authorization_expired")])
def test_capture_of_seeded_closed_hold(make_world, status, code):
    w = make_world(m.fixture(authorizations=[m.hold("a_1", "ada", "bob", 100, status=status)]))
    before = w.oracle()
    expect_error(w.bob.capture("a_1", {}), 409, code)
    assert w.oracle() == before


def test_capture_of_a_seeded_open_hold_past_its_expiry(make_world):
    """[S2-R9] an open hold whose expires_at passed is expired -> 409 authorization_expired."""
    past = m.iso(m.now() - timedelta(hours=2))
    w = make_world(m.fixture(authorizations=[m.hold("a_1", "ada", "bob", 100,
                                                    expires_at=past)]))
    expect_error(w.bob.capture("a_1", {}), 409, "authorization_expired")
    assert w.bob.auth("a_1")["status"] == "expired"
    w.oracle()


# ---- errors ------------------------------------------------------------------

@pytest.mark.parametrize("amount", [0, -1, 1.5, "100", True, None, [], {}])
def test_invalid_capture_amounts(world, amount):
    a = _hold(world)
    before = world.oracle()
    expect_error(world.bob.capture(a["authorization_id"], {"amount": amount}), 422,
                 "validation_failed")
    assert world.oracle() == before


def test_capture_amount_above_the_service_maximum_is_422(world):
    """[§4, §5] above 1000000000 is out of range (and above the remainder): a 422 either way."""
    a = _hold(world)
    before = world.oracle()
    expect_one_of(world.bob.capture(a["authorization_id"], {"amount": 1_000_000_001}),
                  (422, "validation_failed"), (422, "capture_exceeds_authorization"))
    assert world.oracle() == before


@pytest.mark.parametrize("raw", ["1500.0", "1.5e3", "15000e-1"])
def test_integral_capture_literals(world, raw):
    a = _hold(world)
    resp = world.bob.post(f"/authorizations/{a['authorization_id']}/capture",
                          content='{"amount": %s}' % raw, key=new_key())
    assert expect(resp, 201).json()["amount"] == 1_500


@pytest.mark.parametrize("final", ["true", 1, 0, None, [], {}])
def test_final_must_be_a_boolean(world, final):
    """[S2-R6, D25] a non-boolean final is a wrong type: 400 malformed_request."""
    a = _hold(world)
    before = world.oracle()
    expect_error(world.bob.capture(a["authorization_id"], {"amount": 5, "final": final}),
                 400, "malformed_request")
    assert world.oracle() == before


def test_only_the_receiver_may_capture(world):
    a = _hold(world)
    before = world.oracle()
    expect_error(world.ada.capture(a["authorization_id"]), 403, "forbidden")
    expect_error(world.cy.capture(a["authorization_id"]), 403, "forbidden")
    assert world.oracle() == before


def test_unknown_authorization(world):
    expect_error(world.bob.capture("a_does_not_exist"), 404, "not_found")
    expect_error(world.bob.capture("x" * 300), 404, "not_found")


def test_capture_needs_token_and_key(world):
    a = _hold(world)
    path = f"/authorizations/{a['authorization_id']}/capture"
    expect_error(world.bob.post(path, json={}, token=None, key=new_key()), 401,
                 "unauthenticated")
    expect_error(world.bob.post(path, json={}), 400, "missing_idempotency_key")
    expect_error(world.bob.post(path, json={}, headers={"Idempotency-Key": ""}), 400,
                 "missing_idempotency_key")
    expect_error(world.bob.post(path, json={}, key="k" * 256), 422, "validation_failed")
    assert world.ada.me()["held"] == 2_000


@pytest.mark.parametrize("raw", ["", "{", "[]", "null", "1"])
def test_capture_body_must_be_an_object(world, raw):
    a = _hold(world)
    expect_error(world.bob.post(f"/authorizations/{a['authorization_id']}/capture",
                                content=raw or b"", key=new_key()), 400, "malformed_request")


# ---- D25 precedence pairs -------------------------------------------------------------

def _seeded_world(make_world):
    past = m.iso(m.now() - timedelta(hours=3))
    return make_world(m.fixture(authorizations=[
        m.hold("a_open", "ada", "bob", 1_000),
        m.hold("a_cap", "ada", "bob", 1_000, status="captured"),
        m.hold("a_void", "ada", "bob", 1_000, status="voided"),
        m.hold("a_exp", "ada", "bob", 1_000, status="expired"),
        m.hold("a_clock", "ada", "bob", 1_000, expires_at=past),
        m.hold("a_void_past", "ada", "bob", 1_000, status="voided", expires_at=past),
        m.hold("a_cap_past", "ada", "bob", 1_000, status="captured", expires_at=past),
    ]))


@pytest.mark.parametrize("caller,aid,body,status,code", [
    ("bob", "a_missing", {"amount": 0}, 422, "validation_failed"),       # 422 amount > 404
    ("bob", "a_missing", {"amount": 5, "final": "no"}, 400, "malformed_request"),
    ("cy", "a_missing", {}, 404, "not_found"),                            # 404 > 403
    ("ada", "a_open", {"amount": "x"}, 422, "validation_failed"),         # 422 > 403
    ("cy", "a_cap", {}, 403, "forbidden"),                                # 403 > 409
    ("ada", "a_exp", {}, 403, "forbidden"),
    ("ada", "a_clock", {"amount": 9_999}, 403, "forbidden"),
    ("bob", "a_cap", {"amount": 9_999}, 409, "authorization_not_open"),  # 409 > exceeds
    ("bob", "a_void", {"amount": 9_999}, 409, "authorization_not_open"),
    ("bob", "a_exp", {"amount": 9_999}, 409, "authorization_expired"),   # expired > exceeds
    ("bob", "a_clock", {"amount": 9_999}, 409, "authorization_expired"),
    ("bob", "a_void_past", {}, 409, "authorization_not_open"),           # not_open > expired
    ("bob", "a_cap_past", {}, 409, "authorization_not_open"),
    ("bob", "a_open", {"amount": 1_001}, 422, "capture_exceeds_authorization"),
    ("bob", "a_cap", {"amount": 0}, 422, "validation_failed"),           # 422 amount > 409
    ("bob", "a_exp", {"final": 1}, 400, "malformed_request"),
], ids=["amount-over-404", "final-type-over-404", "404-over-403", "amount-over-403",
        "403-over-not-open", "403-over-expired", "403-over-clock-expired",
        "not-open-over-exceeds", "voided-over-exceeds", "expired-over-exceeds",
        "clock-expired-over-exceeds", "voided-past-is-not-open", "captured-past-is-not-open",
        "exceeds", "amount-over-not-open", "type-over-expired"])
def test_capture_precedence(make_world, caller, aid, body, status, code):
    w = _seeded_world(make_world)
    before = w.oracle()
    expect_error(w.clients[caller].capture(aid, body), status, code)
    assert w.oracle() == before
