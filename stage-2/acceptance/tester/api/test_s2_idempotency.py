"""S2 seven idempotent write paths (§7 applied independently to each), with the stage-2
specifics: capture `{}` vs `{"amount": n}`, replays after the hold closes."""
from __future__ import annotations

import json
import time

import pytest

import pf_model as m
from pf_client import expect, expect_error, new_key


def _case(w, name):
    """(caller, path, body, other_body, invalid_body) for one idempotent write path."""
    if name == "payments":
        return (w.ada, "/payments", {"to_handle": "bob", "amount": 100, "note": "n"},
                {"to_handle": "bob", "amount": 101, "note": "n"},
                {"to_handle": "bob", "amount": "lots"})
    if name == "requests":
        return (w.bob, "/requests", {"payer_handle": "ada", "amount": 100, "note": "n"},
                {"payer_handle": "ada", "amount": 100, "note": "m"},
                {"payer_handle": "bob", "amount": 100})
    if name == "pay":
        rid = expect(w.bob.ask("ada", 100), 201).json()["request_id"]
        return (w.ada, f"/requests/{rid}/pay", {"visibility": "private"},
                {"visibility": "public"}, {"visibility": "secret"})
    if name == "splits":
        return (w.ada, "/splits", {"amount": 300, "participant_handles": ["ada", "bob", "cy"]},
                {"amount": 300, "participant_handles": ["bob", "ada", "cy"]},
                {"amount": 300, "participant_handles": []})
    if name == "settlements":
        return (w.ada, "/settlements",
                {"transfers": [{"from_handle": "bob", "to_handle": "cy", "amount": 100}]},
                {"transfers": [{"from_handle": "bob", "to_handle": "cy", "amount": 99}]},
                {"transfers": []})
    if name == "authorizations":
        return (w.ada, "/authorizations",
                {"to_handle": "bob", "amount": 100, "note": "n", "visibility": "private"},
                {"to_handle": "bob", "amount": 100, "note": "n"},
                {"to_handle": "ada", "amount": 100})
    if name == "capture":
        aid = expect(w.ada.authorize("bob", 1_000), 201).json()["authorization_id"]
        return (w.bob, f"/authorizations/{aid}/capture", {"amount": 100, "final": False},
                {"amount": 100}, {"amount": 0})
    raise AssertionError(name)


PATHS = ["payments", "requests", "pay", "splits", "settlements", "authorizations", "capture"]


@pytest.fixture
def iw(make_world):
    """Ada (operator) 10000, Bob 2500, Cy 500."""
    return make_world(m.fixture(operators=["u_ada"]))


def _snapshot(w):
    return (w.oracle(), {h: c.feed() for h, c in w.clients.items()},
            {h: c.requests_list() for h, c in w.clients.items()},
            {h: c.auths() for h, c in w.clients.items()})


@pytest.mark.parametrize("name", PATHS)
@pytest.mark.parametrize("header", [None, ""])
def test_missing_or_empty_key(iw, name, header):
    caller, path, body, _, _ = _case(iw, name)
    before = _snapshot(iw)
    hdrs = {} if header is None else {"Idempotency-Key": header}
    expect_error(caller.post(path, json=body, headers=hdrs), 400, "missing_idempotency_key")
    assert _snapshot(iw) == before


@pytest.mark.parametrize("name", PATHS)
def test_key_length_bounds(iw, name):
    caller, path, body, other, _ = _case(iw, name)
    expect_error(caller.post(path, json=body, key="k" * 256), 422, "validation_failed")
    expect(caller.post(path, json=body, key="k" * 255), 201)
    if name != "pay":
        expect(caller.post(path, json=other, key="z"), 201)
    iw.oracle()


@pytest.mark.parametrize("name", PATHS)
def test_replay_returns_200_with_identical_body_and_no_effect(iw, name):
    caller, path, body, _, _ = _case(iw, name)
    key = new_key()
    first = expect(caller.post(path, json=body, key=key), 201).json()
    after_first = _snapshot(iw)
    for _ in range(3):
        assert expect(caller.post(path, json=body, key=key), 200).json() == first
    assert _snapshot(iw) == after_first


@pytest.mark.parametrize("name", PATHS)
def test_same_key_different_body_is_409(iw, name):
    caller, path, body, other, _ = _case(iw, name)
    key = new_key()
    expect(caller.post(path, json=body, key=key), 201)
    before = _snapshot(iw)
    expect_error(caller.post(path, json=other, key=key), 409, "idempotency_key_reuse")
    assert _snapshot(iw) == before


@pytest.mark.parametrize("name", PATHS)
def test_claimed_key_beats_validation_and_wrong_types(iw, name):
    caller, path, body, _, invalid = _case(iw, name)
    key = new_key()
    expect(caller.post(path, json=body, key=key), 201)
    expect_error(caller.post(path, json=invalid, key=key), 409, "idempotency_key_reuse")
    expect_error(caller.post(path, json={k: 12345 for k in body}, key=key), 409,
                 "idempotency_key_reuse")
    expect_error(caller.post(path, content="{nope", key=key), 400, "malformed_request")
    expect_error(caller.post(path, content="[]", key=key), 400, "malformed_request")


@pytest.mark.parametrize("name", PATHS)
def test_key_order_and_whitespace_do_not_matter(iw, name):
    caller, path, body, _, _ = _case(iw, name)
    key = new_key()
    first = expect(caller.post(path, content=json.dumps(body), key=key), 201).json()
    rev = dict(reversed(list(body.items())))
    spaced = json.dumps(rev, indent=4, separators=(" ,  ", " :  "))
    assert expect(caller.post(path, content=spaced, key=key), 200).json() == first


@pytest.mark.parametrize("name", PATHS)
def test_concurrent_first_use_takes_effect_once(iw, name):
    caller, path, body, _, _ = _case(iw, name)
    who = next(h for h, c in iw.clients.items() if c is caller)
    clients = [iw.new_client(who) for _ in range(50)]
    key = new_key()
    before = iw.wallets()
    out = m.burst(lambda i: clients[i].post(path, json=body, key=key), 50)
    m.assert_no_5xx(out)
    assert m.tally(out) == {200: 49, 201: 1}, m.tally(out)
    bodies = [r.json() for r in out]
    assert all(b == bodies[0] for b in bodies)
    after = iw.oracle()
    if name == "authorizations":
        assert after["ada"][2] == before["ada"][2] + 100
        assert len(iw.ada.auths()) == 1
    if name == "capture":
        assert after["bob"][0] == before["bob"][0] + 100
    if name == "payments":
        assert after["ada"][0] == before["ada"][0] - 100


# ---- stage-2 specifics ------------------------------------------------------------------

def _hold(w, amount=2_000, frm="ada", to="bob", **extra):
    return expect(w.clients[frm].authorize(to, amount, **extra), 201).json()


@pytest.mark.parametrize("first,second", [
    ({}, {"amount": 2_000}),
    ({"amount": 2_000}, {}),
    ({"amount": 700, "final": False}, {"amount": 700}),
    ({"amount": 700}, {"amount": 700, "final": True}),
    ({"final": True}, {}),
])
def test_capture_bodies_that_mean_the_same_are_still_different(iw, first, second):
    """[S2-R6, §7] different JSON values under one key -> 409, even if equivalent."""
    a = _hold(iw)
    key = new_key()
    expect(iw.bob.capture(a["authorization_id"], first, key=key), 201)
    before = _snapshot(iw)
    expect_error(iw.bob.capture(a["authorization_id"], second, key=key), 409,
                 "idempotency_key_reuse")
    assert _snapshot(iw) == before


def test_numerically_equal_capture_amounts_are_the_same_body(iw):
    a = _hold(iw)
    path = f"/authorizations/{a['authorization_id']}/capture"
    key = new_key()
    first = expect(iw.bob.post(path, content='{"amount": 700}', key=key), 201).json()
    for raw in ('{"amount": 700.0}', '{"amount": 7e2}', '{ "amount" : 7.00E2 }'):
        assert expect(iw.bob.post(path, content=raw, key=key), 200).json() == first


@pytest.mark.parametrize("closer", ["final", "void", "partial-then-void"])
def test_capture_replay_after_the_hold_closed(iw, closer):
    """[S2-R6] a replay returns 200 with the original payment even after the hold closed."""
    a = _hold(iw)
    aid = a["authorization_id"]
    key = new_key()
    body = {"amount": 300, "final": False}
    first = expect(iw.bob.capture(aid, body, key=key), 201).json()
    if closer == "final":
        expect(iw.bob.capture(aid, {}), 201)
    elif closer == "void":
        expect(iw.ada.void(aid), 200)
    else:
        expect(iw.bob.capture(aid, {"amount": 5, "final": False}), 201)
        expect(iw.ada.void(aid), 200)
    before = _snapshot(iw)
    assert expect(iw.bob.capture(aid, body, key=key), 200).json() == first
    assert _snapshot(iw) == before


def test_capture_replay_after_expiry(make_world):
    w = make_world(m.fixture(ttl=2))
    a = _hold(w)
    key = new_key()
    first = expect(w.bob.capture(a["authorization_id"], {"amount": 1, "final": False},
                                 key=key), 201).json()
    time.sleep(max(0.0, (m.parse(a["expires_at"]) - m.now()).total_seconds() + 0.4))
    assert w.bob.auth(a["authorization_id"])["status"] == "expired"
    assert expect(w.bob.capture(a["authorization_id"], {"amount": 1, "final": False},
                                key=key), 200).json() == first
    w.oracle()


def test_authorization_replay_returns_the_original_response_after_void(iw):
    """[§7] the original (open) response, even though the hold is now voided."""
    key = new_key()
    first = expect(iw.ada.authorize("bob", 500, key=key), 201).json()
    expect(iw.ada.void(first["authorization_id"]), 200)
    replay = expect(iw.ada.authorize("bob", 500, key=key), 200).json()
    assert replay == first and replay["status"] == "open"
    assert iw.ada.me()["held"] == 0 and len(iw.ada.auths()) == 1


def test_authorization_replay_creates_no_hold_even_when_now_unaffordable(iw):
    key = new_key()
    first = expect(iw.cy.authorize("bob", 500, key=key), 201).json()
    assert expect(iw.cy.authorize("bob", 500, key=key), 200).json() == first
    assert iw.cy.me()["held"] == 500 and len(iw.cy.auths()) == 1
    iw.oracle()


def test_failed_capture_leaves_the_key_free(iw):
    a = _hold(iw)
    key = new_key()
    expect_error(iw.bob.capture(a["authorization_id"], {"amount": 2_001}, key=key), 422,
                 "capture_exceeds_authorization")
    expect_error(iw.ada.capture(a["authorization_id"], {"amount": 1}, key=key), 403,
                 "forbidden")
    expect(iw.bob.capture(a["authorization_id"], {"amount": 2_000}, key=key), 201)
    iw.oracle()


def test_failed_authorization_leaves_the_key_free(iw):
    key = new_key()
    expect_error(iw.cy.authorize("bob", 600, key=key), 409, "insufficient_funds")
    expect(iw.ada.pay("cy", 100), 201)
    expect(iw.cy.authorize("bob", 600, key=key), 201)
    iw.oracle()


def test_same_key_on_two_authorizations_capture_paths(iw):
    """[§7] the same key and body on a different path is a different request."""
    a1, a2 = _hold(iw, 100), _hold(iw, 100)
    key = new_key()
    p1 = expect(iw.bob.capture(a1["authorization_id"], {}, key=key), 201).json()
    p2 = expect(iw.bob.capture(a2["authorization_id"], {}, key=key), 201).json()
    assert p1["payment_id"] != p2["payment_id"]
    iw.oracle()


def test_same_key_and_body_on_payments_and_authorizations(iw):
    key = new_key()
    body = {"to_handle": "bob", "amount": 100}
    p = expect(iw.ada.post("/payments", json=body, key=key), 201).json()
    a = expect(iw.ada.post("/authorizations", json=body, key=key), 201).json()
    assert "payment_id" in p and "authorization_id" in a
    me = iw.ada.me()
    assert (me["total"], me["held"]) == (9_900, 100)
    iw.oracle()


def test_keys_are_scoped_per_user_on_new_paths(iw):
    key = new_key()
    a = expect(iw.ada.authorize("cy", 100, key=key), 201).json()
    b = expect(iw.bob.authorize("cy", 100, key=key), 201).json()
    assert a["authorization_id"] != b["authorization_id"]
    expect(iw.cy.capture(a["authorization_id"], {}, key=key), 201)
    expect(iw.cy.capture(b["authorization_id"], {}, key=key), 201)
    iw.oracle()


def test_capture_replay_from_another_session(iw):
    a = _hold(iw)
    key = new_key()
    first = expect(iw.bob.capture(a["authorization_id"], {}, key=key), 201).json()
    other = iw.new_client("bob")
    assert expect(other.capture(a["authorization_id"], {}, key=key), 200).json() == first


def test_concurrent_capture_same_key_different_bodies(iw):
    a = _hold(iw)
    clients = [iw.new_client("bob") for _ in range(20)]
    key = new_key()
    out = m.burst(lambda i: clients[i].capture(a["authorization_id"],
                                               {"amount": 100 + (i % 4), "final": False},
                                               key=key), 20)
    m.assert_no_5xx(out)
    winners = [r for r in out if r.status_code == 201]
    assert len(winners) == 1, m.tally(out)
    won = winners[0].json()["amount"]
    for i, r in enumerate(out):
        if r.status_code == 200:
            assert 100 + (i % 4) == won and r.json() == winners[0].json()
        elif r.status_code != 201:
            expect_error(r, 409, "idempotency_key_reuse")
    assert iw.ada.auth(a["authorization_id"])["captured_amount"] == won
    iw.oracle()
