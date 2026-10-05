"""§8 GET /me and POST /payments, §4 amounts, §5 field rules."""
from __future__ import annotations

import os

import pytest

import pf_model as m
from pf_client import expect, expect_error, new_key

# DECISION PF_STAGE: run against a later stage's build with PF_STAGE=2 (stage-2 adds fields)
PF_STAGE = int(os.environ.get("PF_STAGE", "1"))

PAYMENT_FIELDS = {"payment_id", "from_user_id", "from_handle", "to_user_id", "to_handle",
                  "amount", "currency", "note", "visibility", "request_id", "created_at",
                  "settlement_id"}


def test_me_shape(world):
    """[§8 GET /me]"""
    stage1 = {"user_id": "u_ada", "display_name": "Ada", "handle": "ada",
              "balance": 10_000, "currency": "EUR", "minor_units": 2}
    me = world.ada.me()
    if PF_STAGE >= 2:
        assert {k: me.get(k) for k in stage1} == stage1 and me["balance"] == me["total"]
    else:
        assert me == stage1


def test_payment_shape_and_effect(world):
    """[§8 POST /payments] 201 with the full payment; debit and credit together."""
    p = expect(world.ada.pay("bob", 1500, note="dinner", visibility="private"), 201).json()
    assert PAYMENT_FIELDS <= set(p), set(p) ^ PAYMENT_FIELDS
    assert (p["from_user_id"], p["from_handle"], p["to_user_id"], p["to_handle"]) == \
        ("u_ada", "ada", "u_bob", "bob")
    assert (p["amount"], p["currency"], p["note"], p["visibility"]) == \
        (1500, "EUR", "dinner", "private")
    assert p["request_id"] is None and p["settlement_id"] is None
    assert world.balances() == {"ada": 8_500, "bob": 4_000, "cy": 500}
    for who in ("ada", "bob"):
        assert world.clients[who].feed() == [p], "the same receipt in both wallets"
    world.oracle()


def test_defaults(world):
    """[§8] note defaults to "", visibility to "public"."""
    p = expect(world.ada.pay("bob", 1), 201).json()
    assert p["note"] == "" and p["visibility"] == "public"


def test_paying_the_whole_balance_reaches_zero(world):
    """[§1.2, §8] balance == amount is affordable; one more unit is not."""
    expect(world.cy.pay("bob", 500), 201)
    assert world.cy.balance() == 0
    expect_error(world.cy.pay("bob", 1), 409, "insufficient_funds")
    world.oracle()


def test_insufficient_funds_leaves_no_trace(world):
    """[§8] a failed payment leaves no trace in either wallet or feed."""
    expect_error(world.cy.pay("bob", 501), 409, "insufficient_funds")
    assert world.balances() == {"ada": 10_000, "bob": 2_500, "cy": 500}
    assert world.bob.feed() == [] and world.cy.feed() == []
    world.oracle()


@pytest.mark.parametrize("amount,moved", [(1, 1), (1000.0, 1000), (1e3, 1000),
                                          (1.5e3, 1500), (2500, 2500)])
def test_integral_amount_forms_are_accepted(world, amount, moved):
    """[§4] 1000, 1000.0 and 1e3 are the same valid amount."""
    p = expect(world.ada.pay("bob", amount), 201).json()
    assert p["amount"] == moved and isinstance(p["amount"], int)
    assert world.ada.balance() == 10_000 - moved


@pytest.mark.parametrize("raw", ["1E3", "1e+3", "10.000e2", "1000.000"])
def test_integral_amount_raw_forms(world, raw):
    """[§4] exponent and trailing-zero forms written literally in the JSON text."""
    resp = world.ada.post("/payments", content='{"to_handle":"bob","amount":%s}' % raw,
                          key=new_key())
    assert expect(resp, 201).json()["amount"] == 1000


def test_max_amount_is_accepted(make_world):
    """[§4] amount at most 1000000000 is valid at the boundary."""
    w = make_world(m.fixture([m.user("rich", 2_000_000_000), m.user("bob", 0)]))
    expect(w.rich.pay("bob", 1_000_000_000), 201)
    expect(w.rich.pay("bob", 1e9), 201)
    assert w.bob.balance() == 2_000_000_000
    expect_error(w.bob.pay("rich", 1_000_000_001), 422, "validation_failed")
    w.oracle()


BAD_AMOUNTS = [0, -1, -0.0, 0.5, 1.5, 100.25, 1_000_000_001, 1e10, 10**20, 2**63, 2**64,
               "100", "1e3", "", True, False, None, [100], {"v": 100}]


@pytest.mark.parametrize("amount", BAD_AMOUNTS, ids=[repr(a) for a in BAD_AMOUNTS])
def test_invalid_amounts_are_422(world, amount):
    """[§5] invalid amount values, including strings, booleans, null -> 422."""
    expect_error(world.ada.pay("bob", amount), 422, "validation_failed")
    assert world.ada.balance() == 10_000


@pytest.mark.parametrize("raw", ["1e400", "-1e400", "1e-400", "123456789012345678901234567890",
                                 "1.0000001e3", "0.1e1", "1000.0000000000001", "1e-1",
                                 "10000e-1"])
def test_huge_and_tiny_numeric_literals(world, raw):
    """[§4, §5, D3] judged on the exact literal: overflowing or fractional -> 422.

    0.1e1 == 1 and 10000e-1 == 1000 are integral and therefore valid.
    """
    resp = world.ada.post("/payments", content='{"to_handle":"bob","amount":%s}' % raw,
                          key=new_key())
    if raw in ("0.1e1", "10000e-1"):
        assert expect(resp, 201).json()["amount"] == {"0.1e1": 1, "10000e-1": 1000}[raw]
    else:
        expect_error(resp, 422, "validation_failed")


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_tokens(world, raw):
    """[§4, §5, D3] NaN/Infinity are not JSON -> 400 malformed_request, never 5xx/201."""
    resp = world.ada.post("/payments", content='{"to_handle":"bob","amount":%s}' % raw,
                          key=new_key())
    expect_error(resp, 400, "malformed_request")
    assert world.ada.balance() == 10_000


def test_missing_amount_and_missing_handle_are_422(world):
    """[§5] a required field missing -> 422 validation_failed."""
    expect_error(world.ada.post("/payments", json={"to_handle": "bob"}, key=new_key()),
                 422, "validation_failed")
    expect_error(world.ada.post("/payments", json={"amount": 5}, key=new_key()),
                 422, "validation_failed")
    expect_error(world.ada.post("/payments", json={}, key=new_key()), 422, "validation_failed")


@pytest.mark.parametrize("handle", [5, True, ["bob"], {"h": "bob"}, None])
def test_wrong_type_handle_is_malformed(world, handle):
    """[§5] to_handle of the wrong JSON type -> 400 malformed_request."""
    expect_error(world.ada.pay(handle, 1), 400, "malformed_request")


def test_self_payment(world):
    """[§8] to_handle is the caller's own handle -> 422 self_payment."""
    expect_error(world.ada.pay("ada", 1), 422, "self_payment")


@pytest.mark.parametrize("handle", ["nobody", "ADA", "Bob", "bob ", "", "a" * 21])
def test_unknown_handle_is_404(world, handle):
    """[§8, D7] any string naming no user (handles are exact) -> 404 not_found."""
    expect_error(world.ada.pay(handle, 1), 404, "not_found")
    assert world.ada.balance() == 10_000


@pytest.mark.parametrize("note,ok", [("x" * 200, True), ("x" * 201, False),
                                     ("\U0001F600" * 200, True), ("\U0001F600" * 201, False),
                                     ("", True)])
def test_note_length_boundary(world, note, ok):
    """[§8] note longer than 200 characters -> 422."""
    resp = world.ada.pay("bob", 1, note=note)
    if ok:
        assert expect(resp, 201).json()["note"] == note
    else:
        expect_error(resp, 422, "validation_failed")


@pytest.mark.parametrize("note", [None, 5, True, ["x"], {"x": 1}])
def test_non_string_note_is_422(world, note):
    """[§5] non-string note values, including null -> 422 validation_failed."""
    expect_error(world.ada.pay("bob", 1, note=note), 422, "validation_failed")


@pytest.mark.parametrize("vis", ["PUBLIC", "Private", "friends", "", None, 1, True, ["public"]])
def test_bad_visibility_is_422(world, vis):
    """[§5] any visibility other than public/private -> 422 validation_failed."""
    expect_error(world.ada.pay("bob", 1, visibility=vis), 422, "validation_failed")


NOTES = ["  padded  ", "<b>&amp;</b>", "line\nbreak\ttab", "caf\u00e9 \u00e9\u0301",
         "\U0001F468\u200d\U0001F469\u200d\U0001F467 \U0001F1EA\U0001F1FA", "\u202erlo",
         "\\\"quoted\\\"", "\u0000nul"]


@pytest.mark.parametrize("note", NOTES, ids=range(len(NOTES)))
def test_note_round_trips_verbatim(world, note):
    """[§8] no trimming, escaping or normalisation; unicode/emoji byte for byte."""
    p = expect(world.ada.pay("bob", 1, note=note), 201).json()
    assert p["note"] == note
    fed = next(x for x in world.bob.feed() if x["payment_id"] == p["payment_id"])
    assert fed["note"] == note


def test_body_must_be_an_object(world):
    """[§5] unparseable or non-object body -> 400 malformed_request."""
    for raw in ["", "{", "{\"to_handle\": \"bob\", }", "[]", "[{\"to_handle\":\"bob\"}]",
                "\"bob\"", "1", "null", "true"]:
        expect_error(world.ada.post("/payments", content=raw or b"", key=new_key()),
                     400, "malformed_request")
    assert world.ada.balance() == 10_000


def test_invalid_utf8_body_is_malformed(world):
    """[§5] a body that does not decode does not parse."""
    raw = b'{"to_handle":"bob","amount":1,"note":"\xff\xfe"}'
    expect_error(world.ada.post("/payments", content=raw, key=new_key()),
                 400, "malformed_request")


# ---- precedence (DECISION-dependent; see README) ----------------------------

def test_precedence_amount_before_self(world):
    """[§5 order] amount rule precedes self_payment."""
    expect_error(world.ada.pay("ada", 0), 422, "validation_failed")


def test_precedence_self_before_note_and_visibility(world):
    expect_error(world.ada.pay("ada", 1, note="x" * 201), 422, "self_payment")
    expect_error(world.ada.pay("ada", 1, visibility="nope"), 422, "self_payment")


def test_precedence_field_rules_before_not_found(world):
    expect_error(world.ada.pay("nobody", 0), 422, "validation_failed")
    expect_error(world.ada.pay("nobody", 1, note="x" * 201), 422, "validation_failed")
    expect_error(world.ada.pay("nobody", 1, visibility="x"), 422, "validation_failed")


def test_precedence_not_found_and_rules_before_funds(world):
    expect_error(world.cy.pay("nobody", 10_000), 404, "not_found")
    expect_error(world.cy.pay("cy", 10_000), 422, "self_payment")
    expect_error(world.cy.pay("bob", 10_000, note="x" * 201), 422, "validation_failed")


def test_precedence_malformed_before_auth(world, api):
    """[§5 order, dispatch risk 3] unparseable body beats a missing token."""
    expect_error(api().post("/payments", content="{", token=None, key=new_key()),
                 400, "malformed_request")


def test_precedence_auth_before_missing_key_and_fields(world, api):
    expect_error(api().post("/payments", json={"to_handle": "ada", "amount": 0}, token=None),
                 401, "unauthenticated")


def test_precedence_missing_key_before_fields(world):
    expect_error(world.ada.post("/payments", json={"to_handle": "ada", "amount": 0}),
                 400, "missing_idempotency_key")
    expect_error(world.ada.post("/payments", json={"to_handle": "nobody", "amount": 1}),
                 400, "missing_idempotency_key")


def test_precedence_wrong_type_400_beats_any_422(world):
    """[D1 step 7, DECISION Q3] a 400 wrong-type field wins over a 422 on another field."""
    expect_error(world.ada.pay(5, 0), 400, "malformed_request")
    expect_error(world.ada.pay(None, 1, note="x" * 201, visibility="nope"),
                 400, "malformed_request")
    expect_error(world.ada.post("/requests", json={"payer_handle": ["bob"], "amount": "x"},
                                key=new_key()), 400, "malformed_request")
    expect_error(world.ada.split(0, "bob"), 400, "malformed_request")
    expect_error(world.ada.split(10, ["bob", 7], note=None), 400, "malformed_request")
