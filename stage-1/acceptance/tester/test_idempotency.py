"""§7 idempotency on all five write paths, with the invariant oracle after each."""
from __future__ import annotations

import json

import pytest

import pf_model as m
from pf_client import Api, expect, expect_error, new_key


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
    raise AssertionError(name)


PATHS = ["payments", "requests", "pay", "splits", "settlements"]


@pytest.fixture
def iw(make_world):
    """Ada (operator) 10000, Bob 2500, Cy 500."""
    return make_world(m.fixture(operators=["u_ada"]))


@pytest.mark.parametrize("name", PATHS)
@pytest.mark.parametrize("header", [None, ""])
def test_missing_or_empty_key(iw, name, header):
    """[§7] header absent or empty -> 400 missing_idempotency_key; nothing happens."""
    caller, path, body, _, _ = _case(iw, name)
    before = iw.oracle()
    hdrs = {} if header is None else {"Idempotency-Key": header}
    expect_error(caller.post(path, json=body, headers=hdrs), 400, "missing_idempotency_key")
    assert iw.oracle() == before


@pytest.mark.parametrize("name", PATHS)
def test_key_length_bounds(iw, name):
    """[§5] key 1..255 characters; 256 -> 422 validation_failed."""
    caller, path, body, other, _ = _case(iw, name)
    expect_error(caller.post(path, json=body, key="k" * 256), 422, "validation_failed")
    expect(caller.post(path, json=body, key="k" * 255), 201)
    if name != "pay":
        expect(caller.post(path, json=other, key="z"), 201)
    iw.oracle()


@pytest.mark.parametrize("name", PATHS)
def test_replay_returns_200_with_identical_body_and_no_effect(iw, name):
    """[§7] replay: same key, same body -> 200, identical JSON, no further state change."""
    caller, path, body, _, _ = _case(iw, name)
    key = new_key()
    first = expect(caller.post(path, json=body, key=key), 201).json()
    after_first = iw.oracle()
    feeds = {h: c.feed() for h, c in iw.clients.items()}
    rqs = {h: c.requests_list() for h, c in iw.clients.items()}
    for _ in range(3):
        assert expect(caller.post(path, json=body, key=key), 200).json() == first
    assert iw.oracle() == after_first
    assert {h: c.feed() for h, c in iw.clients.items()} == feeds
    assert {h: c.requests_list() for h, c in iw.clients.items()} == rqs


@pytest.mark.parametrize("name", PATHS)
def test_same_key_different_body_is_409(iw, name):
    """[§7] same key, different body -> 409 idempotency_key_reuse; no effect."""
    caller, path, body, other, _ = _case(iw, name)
    key = new_key()
    expect(caller.post(path, json=body, key=key), 201)
    before = iw.oracle()
    expect_error(caller.post(path, json=other, key=key), 409, "idempotency_key_reuse")
    assert iw.oracle() == before


@pytest.mark.parametrize("name", PATHS)
def test_claimed_key_beats_field_validation(iw, name):
    """[§7] an already-claimed key is resolved before field validation."""
    caller, path, body, _, invalid = _case(iw, name)
    key = new_key()
    expect(caller.post(path, json=body, key=key), 201)
    expect_error(caller.post(path, json=invalid, key=key), 409, "idempotency_key_reuse")
    expect_error(caller.post(path, json={}, key=key), 409, "idempotency_key_reuse")


@pytest.mark.parametrize("name", PATHS)
def test_claimed_key_beats_wrong_field_types(iw, name):
    """[§7] parsed JSON object + authenticated is enough to resolve the claim (DECISION Q2)."""
    caller, path, body, _, _ = _case(iw, name)
    key = new_key()
    expect(caller.post(path, json=body, key=key), 201)
    weird = {k: 12345 for k in body}
    expect_error(caller.post(path, json=weird, key=key), 409, "idempotency_key_reuse")


@pytest.mark.parametrize("name", PATHS)
def test_unparseable_body_with_claimed_key_is_still_malformed(iw, name):
    """[§7] the claim is only checked after the body parses as a JSON object."""
    caller, path, body, _, _ = _case(iw, name)
    key = new_key()
    expect(caller.post(path, json=body, key=key), 201)
    expect_error(caller.post(path, content="{nope", key=key), 400, "malformed_request")
    expect_error(caller.post(path, content="[]", key=key), 400, "malformed_request")


@pytest.mark.parametrize("name", PATHS)
def test_key_order_and_whitespace_do_not_matter(iw, name):
    """[§7] same body = same JSON value after parsing."""
    caller, path, body, _, _ = _case(iw, name)
    key = new_key()
    first = expect(caller.post(path, content=json.dumps(body), key=key), 201).json()
    rev = dict(reversed(list(body.items())))
    spaced = json.dumps(rev, indent=4, separators=(" ,  ", " :  "))
    assert expect(caller.post(path, content=spaced, key=key), 200).json() == first


@pytest.mark.parametrize("name", PATHS)
def test_keys_are_scoped_per_user(iw, name):
    """[§7] another user may use the same key string with no interaction."""
    caller, path, body, _, _ = _case(iw, name)
    key = "shared-key-string"
    expect(caller.post(path, json=body, key=key), 201)
    other = iw.cy
    if name == "payments":
        resp = other.post(path, json={"to_handle": "bob", "amount": 100, "note": "n"}, key=key)
    elif name == "requests":
        resp = other.post(path, json={"payer_handle": "ada", "amount": 100, "note": "n"}, key=key)
    elif name == "pay":
        rid = expect(iw.bob.ask("cy", 100), 201).json()["request_id"]
        resp = other.post(f"/requests/{rid}/pay", json={"visibility": "private"}, key=key)
    elif name == "splits":
        resp = other.post(path, json=body, key=key)
    else:
        # cy is not an operator: her use of the key is judged on its own (403), not 409/200
        expect_error(other.post(path, json=body, key=key), 403, "forbidden")
        return
    expect(resp, 201)
    iw.oracle()


def test_same_key_same_body_different_path_is_not_a_replay(iw):
    """[§7] the identical key and body on a different path is a different request."""
    key = new_key()
    body = {"to_handle": "bob", "payer_handle": "bob", "amount": 100,
            "participant_handles": ["bob"]}
    p = expect(iw.ada.post("/payments", json=body, key=key), 201).json()
    r = expect(iw.ada.post("/requests", json=body, key=key), 201).json()
    s = expect(iw.ada.post("/splits", json=body, key=key), 201).json()
    assert p["amount"] == 100 and r["status"] == "pending" and len(s["requests"]) == 1
    assert len(iw.bob.requests_list()) == 2
    iw.oracle()


def test_same_key_on_two_different_requests_pay_paths(iw):
    """[§7] /requests/A/pay and /requests/B/pay are different paths."""
    a = expect(iw.bob.ask("ada", 10), 201).json()["request_id"]
    b = expect(iw.bob.ask("ada", 20), 201).json()["request_id"]
    key = new_key()
    pa = expect(iw.ada.pay_request(a, {}, key=key), 201).json()
    pb = expect(iw.ada.pay_request(b, {}, key=key), 201).json()
    assert pa["payment_id"] != pb["payment_id"] and (pa["amount"], pb["amount"]) == (10, 20)
    assert iw.ada.balance() == 10_000 - 30
    iw.oracle()


def test_failed_first_use_leaves_the_key_free_422(iw):
    """[§7] a key whose first use failed with 4xx is treated as a first use."""
    key = new_key()
    expect_error(iw.ada.pay("bob", 0, key=key), 422, "validation_failed")
    expect(iw.ada.pay("cy", 7, note="different", key=key), 201)
    iw.oracle()


def test_failed_first_use_409_funds_then_same_body_succeeds_later(iw):
    """[§7, §4] insufficient funds frees the key; after money arrives the same key works."""
    key = new_key()
    expect_error(iw.cy.pay("bob", 600, key=key), 409, "insufficient_funds")
    expect(iw.ada.pay("cy", 100), 201)
    first = expect(iw.cy.pay("bob", 600, key=key), 201).json()
    assert expect(iw.cy.pay("bob", 600, key=key), 200).json() == first
    assert iw.cy.balance() == 0
    iw.oracle()


def test_failed_first_use_404_then_handle_appears(iw, api):
    """[§7] a 404 first use claims nothing."""
    key = new_key()
    expect_error(iw.ada.pay("dee", 5, key=key), 404, "not_found")
    tok = expect(api().signup("dee@example.com"), 201).json()["token"]
    iw.adopt("dee", api(tok))
    expect(iw.ada.pay("dee", 5, key=key), 201)
    assert iw.dee.balance() == 5
    iw.oracle()


def test_failed_first_use_400_and_409_key_reuse_do_not_claim(iw):
    """[§7] malformed or wrong-type first use claims nothing."""
    key = new_key()
    expect_error(iw.ada.post("/payments", content="{", key=key), 400, "malformed_request")
    expect_error(iw.ada.pay(5, 5, key=key), 400, "malformed_request")
    expect(iw.ada.pay("bob", 5, key=key), 201)


def test_failed_pay_request_frees_the_key(iw):
    """[§7] pay fails 409 insufficient_funds; later the same key pays it."""
    rid = expect(iw.bob.ask("cy", 900), 201).json()["request_id"]
    key = new_key()
    expect_error(iw.cy.pay_request(rid, {}, key=key), 409, "insufficient_funds")
    expect(iw.ada.pay("cy", 400), 201)
    expect(iw.cy.pay_request(rid, {}, key=key), 201)
    assert iw.cy.balance() == 0
    iw.oracle()


def test_failed_settlement_validation_claims_no_key(iw):
    """[§11] failed validation claims no idempotency key and creates no payment."""
    key = new_key()
    expect_error(iw.ada.settle([{"from_handle": "bob", "to_handle": "bob", "amount": 1}],
                               key=key), 422, "self_payment")
    expect_error(iw.ada.settle([{"from_handle": "cy", "to_handle": "bob", "amount": 501}],
                               key=key), 409, "insufficient_funds")
    expect(iw.ada.settle([{"from_handle": "cy", "to_handle": "bob", "amount": 500}],
                         key=key), 201)
    assert iw.cy.balance() == 0
    iw.oracle()


def test_pay_request_body_empty_vs_explicit_public_are_different(iw):
    """[§8 pay] {} and {"visibility": "public"} are different JSON values -> 409."""
    rid = expect(iw.bob.ask("ada", 50), 201).json()["request_id"]
    key = new_key()
    first = expect(iw.ada.pay_request(rid, {}, key=key), 201).json()
    assert first["visibility"] == "public"
    expect_error(iw.ada.pay_request(rid, {"visibility": "public"}, key=key),
                 409, "idempotency_key_reuse")
    assert expect(iw.ada.pay_request(rid, {}, key=key), 200).json() == first
    iw.oracle()


def test_payment_note_default_vs_explicit_empty_are_different_bodies(iw):
    """[§7] omission and the default value are different JSON values."""
    key = new_key()
    expect(iw.ada.pay("bob", 5, key=key), 201)
    expect_error(iw.ada.pay("bob", 5, key=key, note=""), 409, "idempotency_key_reuse")


def test_numerically_equal_amount_forms_are_the_same_body(iw):
    """[§4, §7] 1000, 1000.0 and 1e3 are the same JSON value (DECISION Q6)."""
    key = new_key()
    first = expect(iw.ada.pay("bob", 1000, key=key), 201).json()
    for raw in ("1000.0", "1e3"):
        resp = iw.ada.post("/payments", content='{"to_handle":"bob","amount":%s}' % raw, key=key)
        assert expect(resp, 200).json() == first
    assert iw.ada.balance() == 9_000


def test_request_replay_survives_cancellation(iw):
    """[§7] a replay returns the original response even after the resource changed."""
    key = new_key()
    body = {"payer_handle": "ada", "amount": 70}
    first = expect(iw.bob.post("/requests", json=body, key=key), 201).json()
    expect(iw.bob.post(f"/requests/{first['request_id']}/cancel", json={}), 200)
    replay = expect(iw.bob.post("/requests", json=body, key=key), 200).json()
    assert replay == first and replay["status"] == "pending"
    assert [r["status"] for r in iw.bob.requests_list()] == ["cancelled"], "no new request"


def test_pay_replay_after_paid_is_200_not_request_not_pending(iw):
    """[§8 pay] replaying a successful pay returns the payment, moves nothing."""
    rid = expect(iw.bob.ask("ada", 300), 201).json()["request_id"]
    key = new_key()
    first = expect(iw.ada.pay_request(rid, {"visibility": "private"}, key=key), 201).json()
    assert first["request_id"] == rid and first["visibility"] == "private"
    for _ in range(2):
        assert expect(iw.ada.pay_request(rid, {"visibility": "private"}, key=key),
                      200).json() == first
    expect_error(iw.ada.pay_request(rid, {"visibility": "private"}), 409, "request_not_pending")
    assert iw.ada.balance() == 9_700
    iw.oracle()


def test_split_replay_after_its_requests_are_paid(iw):
    """[§7] the split replay is the original body even after requests change status."""
    key = new_key()
    body = {"amount": 30, "participant_handles": ["ada", "bob", "cy"]}
    first = expect(iw.ada.post("/splits", json=body, key=key), 201).json()
    expect(iw.bob.pay_request(first["requests"][0]["request_id"]), 201)
    expect(iw.cy.post(f"/requests/{first['requests'][1]['request_id']}/decline", json={}), 200)
    assert expect(iw.ada.post("/splits", json=body, key=key), 200).json() == first
    assert len(iw.ada.requests_list()) == 2
    iw.oracle()


def test_payment_replay_survives_later_drain(iw):
    """[§7] replay after the sender's balance changed still returns the original 200."""
    key = new_key()
    first = expect(iw.cy.pay("bob", 400, key=key), 201).json()
    expect(iw.cy.pay("bob", 100), 201)
    assert iw.cy.balance() == 0
    assert expect(iw.cy.pay("bob", 400, key=key), 200).json() == first
    assert iw.cy.balance() == 0
    iw.oracle()


def test_replay_from_another_session_of_the_same_user(iw):
    """[§7] scope is the user, not the token."""
    key = new_key()
    first = expect(iw.ada.pay("bob", 11, key=key), 201).json()
    other_session = iw.new_client("ada")
    assert expect(other_session.pay("bob", 11, key=key), 200).json() == first
    assert iw.ada.balance() == 10_000 - 11


@pytest.mark.parametrize("name", PATHS)
def test_concurrent_first_use_takes_effect_once(iw, base_url, name):
    """[§7] concurrent identical requests: exactly one 201, others 200 with the same body."""
    caller, path, body, _, _ = _case(iw, name)
    who = next(h for h, c in iw.clients.items() if c is caller)
    clients = [iw.new_client(who) for _ in range(50)]
    key = new_key()
    before_rqs = len(caller.requests_list())
    out = m.burst(lambda i: clients[i].post(path, json=body, key=key), 50)
    m.assert_no_5xx(out)
    assert m.tally(out) == {200: 49, 201: 1}, m.tally(out)
    bodies = [r.json() for r in out]
    assert all(b == bodies[0] for b in bodies)
    bal = iw.oracle()
    if name == "payments":
        assert bal["ada"] == 10_000 - 100
    if name == "pay":
        assert bal["ada"] == 10_000 - 100
    if name == "settlements":
        assert bal["cy"] == 600
    if name in ("requests", "splits"):
        created = len(caller.requests_list()) - before_rqs
        assert created == {"requests": 1, "splits": 2}[name], created


def test_concurrent_same_key_different_bodies(iw):
    """[§7] adversarial: same key raced with different bodies: one wins, rest 409/200."""
    clients = [iw.new_client("ada") for _ in range(20)]
    key = new_key()
    out = m.burst(lambda i: clients[i].pay("bob", 100 + (i % 4), key=key), 20)
    m.assert_no_5xx(out)
    winners = [r for r in out if r.status_code == 201]
    assert len(winners) == 1, m.tally(out)
    won = winners[0].json()["amount"]
    for i, r in enumerate(out):
        if r.status_code == 200:
            assert 100 + (i % 4) == won and r.json() == winners[0].json()
        elif r.status_code != 201:
            expect_error(r, 409, "idempotency_key_reuse")
            assert 100 + (i % 4) != won
    assert iw.ada.balance() == 10_000 - won
    iw.oracle()


def test_concurrent_first_use_that_fails_claims_nothing(iw):
    """[§7] racing 4xx first uses leave the key free."""
    clients = [iw.new_client("cy") for _ in range(10)]
    key = new_key()
    out = m.burst(lambda i: clients[i].pay("bob", 501, key=key), 10)
    m.assert_no_5xx(out)
    for r in out:
        expect_error(r, 409, "insufficient_funds")
    expect(iw.cy.pay("bob", 500, key=key), 201)
    iw.oracle()
