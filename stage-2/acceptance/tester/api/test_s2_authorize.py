"""S2-R3 POST /authorizations: shape, defaults, every error, precedence, not a feed item."""
from __future__ import annotations

from datetime import timedelta

import pytest

import pf_model as m
from conftest import AUTH_FIELDS, check_auth
from pf_client import expect, expect_error, new_key


def test_authorization_shape(world):
    """[S2-R3] 201 with every field; open, nothing captured, expires = created + 600 s."""
    a = expect(world.ada.authorize("bob", 2_000, note="deposit", visibility="private"),
               201).json()
    assert AUTH_FIELDS <= set(a), AUTH_FIELDS - set(a)
    assert a["from_user_id"] == "u_ada" and a["from_handle"] == "ada"
    assert a["to_user_id"] == "u_bob" and a["to_handle"] == "bob"
    assert a["amount"] == 2_000 and a["captured_amount"] == 0
    assert a["remaining_amount"] == 2_000
    assert a["currency"] == "EUR" and a["note"] == "deposit" and a["visibility"] == "private"
    assert a["status"] == "open" and a["payment_id"] is None and a["payment_ids"] == []
    assert isinstance(a["authorization_id"], str) and 1 <= len(a["authorization_id"]) <= 64
    check_auth(a)
    assert m.parse(a["expires_at"]) - m.parse(a["created_at"]) == timedelta(seconds=600)
    assert abs((m.parse(a["created_at"]) - m.now()).total_seconds()) < 30
    world.oracle()


def test_defaults_note_empty_visibility_public(world):
    a = expect(world.ada.authorize("bob", 1), 201).json()
    assert a["note"] == "" and a["visibility"] == "public"


@pytest.mark.parametrize("ttl", [1, 61, 3_600, 86_400 * 365])
def test_expires_at_uses_the_fixture_ttl(make_world, ttl):
    """[S2-R2, S2-R3] expires_at is created_at + authorization_ttl_seconds."""
    w = make_world(m.fixture(ttl=ttl))
    a = expect(w.ada.authorize("bob", 5), 201).json()
    assert m.parse(a["expires_at"]) - m.parse(a["created_at"]) == timedelta(seconds=ttl)


def test_listed_identically_to_both_parties_and_nobody_else(world):
    a = expect(world.ada.authorize("bob", 300, visibility="public"), 201).json()
    assert world.ada.auths() == [a]
    assert world.bob.auths() == [a]
    assert world.cy.auths() == []


def test_an_open_hold_is_never_a_feed_item(world):
    """[S2-R3] neither party nor a third party sees an open (or public) hold in /activity."""
    expect(world.ada.authorize("bob", 300, visibility="public"), 201)
    expect(world.ada.authorize("cy", 300, visibility="private"), 201)
    for c in world.clients.values():
        assert c.feed() == []
    world.oracle()


def test_note_round_trips_verbatim(world):
    note = "  café \U0001F37D️ <b>x</b> ‮ rtl \"q\" \\ é "
    a = expect(world.ada.authorize("bob", 1, note=note), 201).json()
    assert a["note"] == note and world.bob.auths()[0]["note"] == note


def test_note_length_boundary(world):
    """[S2-R3] note up to 200 code points; 201 -> 422."""
    expect(world.ada.authorize("bob", 1, note="\U0001F600" * 200), 201)
    expect_error(world.ada.authorize("bob", 1, note="x" * 201), 422, "validation_failed")


@pytest.mark.parametrize("amount", [0, -1, 1_000_000_001, 1.5, "100", True, False, None,
                                    [], {}, 10**30])
def test_invalid_amounts_are_422(world, amount):
    before = world.oracle()
    expect_error(world.ada.authorize("bob", amount), 422, "validation_failed")
    assert world.oracle() == before


def test_missing_amount_and_missing_handle_are_422(world):
    k = new_key()
    expect_error(world.ada.post("/authorizations", json={"to_handle": "bob"}, key=k),
                 422, "validation_failed")
    expect_error(world.ada.post("/authorizations", json={"amount": 5}, key=k),
                 422, "validation_failed")


@pytest.mark.parametrize("handle", [5, None, ["bob"], {"h": "bob"}, True])
def test_wrong_type_handle_is_malformed(world, handle):
    expect_error(world.ada.post("/authorizations", json={"to_handle": handle, "amount": 5},
                                key=new_key()), 400, "malformed_request")


@pytest.mark.parametrize("raw", ["1000.0", "1e3", "10000e-1", "1E3"])
def test_integral_amount_literals_are_accepted(world, raw):
    resp = world.ada.post("/authorizations", content='{"to_handle":"bob","amount":%s}' % raw,
                          key=new_key())
    assert expect(resp, 201).json()["amount"] == 1_000


def test_max_amount_boundary(make_world):
    w = make_world(m.fixture([m.user("rich", 3_000_000_000), m.BOB]))
    a = expect(w.rich.authorize("bob", 1_000_000_000), 201).json()
    assert a["amount"] == 1_000_000_000
    expect_error(w.rich.authorize("bob", 1_000_000_001), 422, "validation_failed")
    assert w.rich.me()["held"] == 1_000_000_000
    w.oracle()


def test_self_authorization(world):
    expect_error(world.ada.authorize("ada", 1), 422, "self_payment")


@pytest.mark.parametrize("handle", ["nobody", "", "Bob", "@bob", " bob"])
def test_unknown_handle_is_404(world, handle):
    expect_error(world.ada.authorize(handle, 1), 404, "not_found")


@pytest.mark.parametrize("vis", ["secret", "PUBLIC", "", None, 1])
def test_bad_visibility(world, vis):
    expect_error(world.ada.authorize("bob", 1, visibility=vis), 422, "validation_failed")


@pytest.mark.parametrize("note", [None, 5, ["x"]])
def test_non_string_note(world, note):
    expect_error(world.ada.authorize("bob", 1, note=note), 422, "validation_failed")


def test_available_boundary(world):
    """[S2-R3] exactly available is fine; one more is 409 and leaves no trace."""
    expect(world.cy.authorize("bob", 200), 201)
    before = world.oracle()
    expect_error(world.cy.authorize("bob", 301), 409, "insufficient_funds")
    assert world.oracle() == before and len(world.cy.auths()) == 1
    expect(world.cy.authorize("bob", 300), 201)
    assert world.cy.me()["available"] == 0
    world.oracle()


def test_new_user_with_zero_balance_cannot_hold(world, api):
    eve = api()
    eve.token = expect(eve.signup("eve@example.com"), 201).json()["token"]
    expect_error(eve.authorize("bob", 1), 409, "insufficient_funds")
    a = expect(world.ada.authorize("eve", 10), 201).json()
    assert a["to_handle"] == "eve"


# ---- precedence (S2-R3 order: amount, self, note, visibility, 404, funds) ------------

@pytest.mark.parametrize("body,status,code", [
    ({"to_handle": "ada", "amount": 0}, 422, "validation_failed"),           # amount > self
    ({"to_handle": "ada", "amount": 5, "note": "x" * 201}, 422, "self_payment"),
    ({"to_handle": "nobody", "amount": 5, "note": "x" * 201}, 422, "validation_failed"),
    ({"to_handle": "nobody", "amount": 5, "visibility": "x"}, 422, "validation_failed"),
    ({"to_handle": "nobody", "amount": 99_999_999}, 404, "not_found"),       # 404 > funds
    ({"to_handle": "bob", "amount": 99_999_999, "visibility": "x"}, 422, "validation_failed"),
    ({"to_handle": "ada", "amount": 99_999_999}, 422, "self_payment"),
    ({"to_handle": 5, "amount": 0}, 400, "malformed_request"),               # type > 422
], ids=["amount-over-self", "self-over-note", "note-over-404", "visibility-over-404",
        "404-over-funds", "visibility-over-funds", "self-over-funds", "type-over-amount"])
def test_precedence(world, body, status, code):
    before = world.oracle()
    expect_error(world.ada.post("/authorizations", json=body, key=new_key()), status, code)
    assert world.oracle() == before


def test_no_token_is_401_before_key_and_body(world):
    expect_error(world.ada.post("/authorizations", json={"to_handle": "bob", "amount": 1},
                                token=None), 401, "unauthenticated")
    expect_error(world.ada.post("/authorizations", json={}, token="nope"),
                 401, "unauthenticated")


def test_missing_key_before_field_validation(world):
    expect_error(world.ada.post("/authorizations", json={"to_handle": "ada", "amount": 0}),
                 400, "missing_idempotency_key")
    expect_error(world.ada.post("/authorizations", json={"to_handle": "bob", "amount": 1},
                                key="k" * 256), 422, "validation_failed")


@pytest.mark.parametrize("raw", ["", "{", "[]", "null", "\"x\"", "7"])
def test_body_must_be_a_json_object(world, raw):
    expect_error(world.ada.post("/authorizations", content=raw or b"", key=new_key()),
                 400, "malformed_request")


def test_unknown_fields_are_ignored(world):
    a = expect(world.ada.authorize("bob", 5, status="captured", captured_amount=5,
                                   expires_at="2000-01-01T00:00:00+00:00", from_handle="cy",
                                   final=False, zzz=[1]), 201).json()
    assert a["status"] == "open" and a["captured_amount"] == 0 and a["from_handle"] == "ada"
    assert m.parse(a["expires_at"]) > m.now()
    world.oracle()


def test_ids_are_unique_and_never_collide_with_seeded_ids(make_world):
    fx = m.fixture(authorizations=[m.hold("a_1", "ada", "bob", 10),
                                   m.hold("a_2", "ada", "bob", 10, status="voided")])
    w = make_world(fx)
    ids = {expect(w.ada.authorize("bob", 1), 201).json()["authorization_id"]
           for _ in range(5)}
    assert len(ids) == 5 and not ids & {"a_1", "a_2"}
    w.oracle()
