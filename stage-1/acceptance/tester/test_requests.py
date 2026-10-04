"""§8 requests: create, pay, decline, cancel, list; ownership and state rules."""
from __future__ import annotations

import pytest

import pf_model as m
from pf_client import expect, expect_error, new_key

REQUEST_FIELDS = {"request_id", "requester_id", "requester_handle", "payer_id", "payer_handle",
                  "amount", "currency", "note", "status", "payment_id", "created_at"}


def _rq(w, amount=1200, payer="ada", requester="bob", **extra) -> dict:
    return expect(w.clients[requester].ask(payer, amount, **extra), 201).json()


def _act(w, who, rid, action):
    return w.clients[who].post(f"/requests/{rid}/{action}", json={})


def test_request_shape(world):
    """[§8 POST /requests] the caller is the requester."""
    r = _rq(world, note="taxi")
    assert REQUEST_FIELDS <= set(r), set(r) ^ REQUEST_FIELDS
    assert (r["requester_id"], r["requester_handle"], r["payer_id"], r["payer_handle"]) == \
        ("u_bob", "bob", "u_ada", "ada")
    assert (r["amount"], r["currency"], r["note"], r["status"], r["payment_id"]) == \
        (1200, "EUR", "taxi", "pending", None)


def test_request_note_defaults_to_empty(world):
    assert _rq(world)["note"] == ""


def test_request_may_exceed_payers_balance(world):
    """[§4, §8] the payer's balance is not checked at creation."""
    r = _rq(world, amount=1_000_000_000, payer="cy")
    assert r["status"] == "pending"
    assert world.balances() == {"ada": 10_000, "bob": 2_500, "cy": 500}


def test_request_moves_no_money_and_is_not_a_feed_item(world):
    """[§4] requests never appear in the activity feed."""
    _rq(world)
    world.oracle()
    for c in world.clients.values():
        assert c.feed() == []


@pytest.mark.parametrize("amount", [0, -5, 1.5, 1_000_000_001, "1200", True, None, [1]])
def test_request_amount_rules(world, amount):
    expect_error(world.bob.ask("ada", amount), 422, "validation_failed")


@pytest.mark.parametrize("amount,stored", [(1, 1), (1_000_000_000, 1_000_000_000),
                                           (12.0, 12), (1.2e3, 1200)])
def test_request_amount_valid_forms(world, amount, stored):
    assert _rq(world, amount=amount)["amount"] == stored


def test_self_request(world):
    expect_error(world.bob.ask("bob", 1), 422, "self_request")


def test_request_note_rules(world):
    assert _rq(world, note="y" * 200)["note"] == "y" * 200
    expect_error(world.bob.ask("ada", 1, note="y" * 201), 422, "validation_failed")
    expect_error(world.bob.ask("ada", 1, note=None), 422, "validation_failed")
    expect_error(world.bob.ask("ada", 1, note=7), 422, "validation_failed")


def test_request_unknown_payer(world):
    expect_error(world.bob.ask("nobody", 1), 404, "not_found")


def test_request_missing_and_wrong_type_payer(world):
    expect_error(world.bob.post("/requests", json={"amount": 1}, key=new_key()),
                 422, "validation_failed")
    expect_error(world.bob.ask(42, 1), 400, "malformed_request")


def test_request_precedence(world):
    """[§5 order] amount -> self_request -> note -> 404 (DECISION Q4)."""
    expect_error(world.bob.ask("bob", 0), 422, "validation_failed")
    expect_error(world.bob.ask("bob", 1, note="z" * 201), 422, "self_request")
    expect_error(world.bob.ask("nobody", 1, note="z" * 201), 422, "validation_failed")
    expect_error(world.bob.ask("nobody", 0), 422, "validation_failed")


# ---- pay -------------------------------------------------------------------

def test_pay_returns_the_payment_and_marks_request_paid(world):
    """[§8 pay] 201 payment shaped like POST /payments with request_id set."""
    r = _rq(world, note="taxi")
    p = expect(world.ada.pay_request(r["request_id"], {"visibility": "private"}), 201).json()
    assert (p["from_handle"], p["to_handle"], p["amount"], p["note"], p["visibility"]) == \
        ("ada", "bob", 1200, "taxi", "private")
    assert p["request_id"] == r["request_id"] and p["settlement_id"] is None
    assert world.balances() == {"ada": 8_800, "bob": 3_700, "cy": 500}
    for who in ("ada", "bob"):
        got = next(x for x in world.clients[who].requests_list()
                   if x["request_id"] == r["request_id"])
        assert got["status"] == "paid" and got["payment_id"] == p["payment_id"]
        assert world.clients[who].feed() == [p]
    assert world.cy.feed() == [], "private: hidden from third parties"
    world.oracle()


def test_pay_visibility_defaults_public(world):
    p = expect(world.ada.pay_request(_rq(world)["request_id"]), 201).json()
    assert p["visibility"] == "public"
    assert world.cy.feed() == [p]


@pytest.mark.parametrize("vis", ["friends", None, 0, ""])
def test_pay_bad_visibility(world, vis):
    r = _rq(world)
    expect_error(world.ada.pay_request(r["request_id"], {"visibility": vis}),
                 422, "validation_failed")
    assert world.ada.requests_list()[0]["status"] == "pending"


def test_pay_while_short_then_later_succeeds(world):
    """[§4] short payer -> 409 insufficient_funds, nothing changes; later payable."""
    r = _rq(world, amount=800, payer="cy")
    expect_error(world.cy.pay_request(r["request_id"]), 409, "insufficient_funds")
    assert world.cy.requests_list()[0]["status"] == "pending"
    world.oracle()
    expect(world.ada.pay("cy", 300), 201)
    expect(world.cy.pay_request(r["request_id"]), 201)
    assert world.cy.balance() == 0
    world.oracle()


@pytest.mark.parametrize("who", ["bob", "cy"])
def test_only_the_payer_may_pay(world, who):
    """[§8] requester or third party -> 403 forbidden; no money moves."""
    r = _rq(world, amount=10)
    expect_error(world.clients[who].pay_request(r["request_id"]), 403, "forbidden")
    world.oracle()
    assert world.ada.requests_list()[0]["status"] == "pending"


@pytest.mark.parametrize("rid", ["rq_nope", "x" * 64, "%20", "0"])
def test_pay_unknown_request(world, rid):
    expect_error(world.ada.pay_request(rid), 404, "not_found")


@pytest.mark.parametrize("prior", ["paid", "declined", "cancelled"])
def test_pay_non_pending(world, prior):
    r = _rq(world, amount=10)
    if prior == "paid":
        expect(world.ada.pay_request(r["request_id"]), 201)
    else:
        expect(_act(world, "ada" if prior == "declined" else "bob", r["request_id"],
                    "decline" if prior == "declined" else "cancel"), 200)
    before = world.oracle()
    expect_error(world.ada.pay_request(r["request_id"]), 409, "request_not_pending")
    assert world.oracle() == before


def test_pay_precedence_permission_before_state_before_funds(world):
    """[§5 order] 404 -> 403 -> 409 request_not_pending -> 409 insufficient_funds."""
    paid = _rq(world, amount=10, payer="cy")
    expect(world.cy.pay_request(paid["request_id"]), 201)
    expect_error(world.bob.pay_request(paid["request_id"]), 403, "forbidden")
    big = _rq(world, amount=5_000, payer="cy")
    expect(_act(world, "bob", big["request_id"], "cancel"), 200)
    expect_error(world.cy.pay_request(big["request_id"]), 409, "request_not_pending")
    expect_error(world.ada.pay_request(big["request_id"]), 403, "forbidden")


def test_pay_bad_visibility_before_state(world):
    """[§5 order] field rule (visibility) before state conflict."""
    r = _rq(world, amount=10)
    expect(_act(world, "ada", r["request_id"], "decline"), 200)
    expect_error(world.ada.pay_request(r["request_id"], {"visibility": "x"}),
                 422, "validation_failed")


@pytest.mark.parametrize("raw", ["{", "[]", "\"public\""])
def test_pay_body_must_be_object(world, raw):
    r = _rq(world, amount=10)
    expect_error(world.ada.post(f"/requests/{r['request_id']}/pay", content=raw, key=new_key()),
                 400, "malformed_request")


def test_pay_requires_key(world):
    r = _rq(world, amount=10)
    expect_error(world.ada.post(f"/requests/{r['request_id']}/pay", json={}),
                 400, "missing_idempotency_key")


# ---- decline / cancel --------------------------------------------------------

def test_decline_by_payer_twice(world):
    """[§8 decline] 200 with the request; twice is 200 with the current state."""
    r = _rq(world)
    a = expect(_act(world, "ada", r["request_id"], "decline"), 200).json()
    assert a["status"] == "declined" and a["request_id"] == r["request_id"]
    assert REQUEST_FIELDS <= set(a)
    b = expect(_act(world, "ada", r["request_id"], "decline"), 200).json()
    assert b == a
    world.oracle()


def test_cancel_by_requester_twice(world):
    r = _rq(world)
    a = expect(_act(world, "bob", r["request_id"], "cancel"), 200).json()
    assert a["status"] == "cancelled" and a["payment_id"] is None
    assert expect(_act(world, "bob", r["request_id"], "cancel"), 200).json() == a


@pytest.mark.parametrize("who", ["bob", "cy"])
def test_only_payer_declines(world, who):
    r = _rq(world)
    expect_error(_act(world, who, r["request_id"], "decline"), 403, "forbidden")
    assert world.bob.requests_list()[0]["status"] == "pending"


@pytest.mark.parametrize("who", ["ada", "cy"])
def test_only_requester_cancels(world, who):
    r = _rq(world)
    expect_error(_act(world, who, r["request_id"], "cancel"), 403, "forbidden")
    assert world.bob.requests_list()[0]["status"] == "pending"


@pytest.mark.parametrize("action,who", [("decline", "ada"), ("cancel", "bob")])
def test_decline_cancel_unknown(world, action, who):
    expect_error(_act(world, who, "rq_missing", action), 404, "not_found")


@pytest.mark.parametrize("prior", ["paid", "cancelled"])
def test_decline_after_paid_or_cancelled(world, prior):
    r = _rq(world, amount=10)
    if prior == "paid":
        expect(world.ada.pay_request(r["request_id"]), 201)
    else:
        expect(_act(world, "bob", r["request_id"], "cancel"), 200)
    expect_error(_act(world, "ada", r["request_id"], "decline"), 409, "request_not_pending")


@pytest.mark.parametrize("prior", ["paid", "declined"])
def test_cancel_after_paid_or_declined(world, prior):
    r = _rq(world, amount=10)
    if prior == "paid":
        expect(world.ada.pay_request(r["request_id"]), 201)
    else:
        expect(_act(world, "ada", r["request_id"], "decline"), 200)
    expect_error(_act(world, "bob", r["request_id"], "cancel"), 409, "request_not_pending")


def test_decline_cancel_need_no_key_or_body(world):
    """[§8] no idempotency key; a missing body is fine."""
    r1, r2 = _rq(world), _rq(world)
    expect(world.ada.request("POST", f"/requests/{r1['request_id']}/decline"), 200)
    expect(world.bob.request("POST", f"/requests/{r2['request_id']}/cancel"), 200)


def test_third_party_permission_precedes_state(world):
    """[§5 order] 403 beats 409 request_not_pending for a non-party."""
    r = _rq(world, amount=10)
    expect(world.ada.pay_request(r["request_id"]), 201)
    expect_error(_act(world, "cy", r["request_id"], "decline"), 403, "forbidden")
    expect_error(_act(world, "cy", r["request_id"], "cancel"), 403, "forbidden")


# ---- GET /requests -------------------------------------------------------------

def test_list_only_my_requests_newest_first(world):
    """[§8 GET /requests] only the caller's, newest first."""
    ids = [_rq(world, amount=i + 1)["request_id"] for i in range(3)]
    ids.append(_rq(world, amount=9, payer="bob", requester="cy")["request_id"])
    assert [r["request_id"] for r in world.bob.requests_list()] == list(reversed(ids))
    assert [r["request_id"] for r in world.ada.requests_list()] == list(reversed(ids[:3]))
    assert [r["request_id"] for r in world.cy.requests_list()] == [ids[3]]


def test_list_direction_and_status_filters(world):
    out1 = _rq(world, amount=1)                         # bob -> asks ada
    inc = _rq(world, amount=2, payer="bob", requester="ada")
    out2 = _rq(world, amount=3, payer="cy")
    expect(_act(world, "cy", out2["request_id"], "decline"), 200)
    ids = lambda **p: [r["request_id"] for r in world.bob.requests_list(**p)]  # noqa: E731
    assert ids(direction="outgoing") == [out2["request_id"], out1["request_id"]]
    assert ids(direction="incoming") == [inc["request_id"]]
    assert ids(status="declined") == [out2["request_id"]]
    assert ids(status="pending", direction="outgoing") == [out1["request_id"]]
    assert ids(status="paid") == [] and ids(status="cancelled") == []


@pytest.mark.parametrize("params", [{"direction": "both"}, {"direction": "INCOMING"},
                                    {"direction": ""}, {"status": "open"}, {"status": "PAID"},
                                    {"status": ""}])
def test_list_bad_filters(world, params):
    expect_error(world.bob.get("/requests", params=params), 422, "validation_failed")


BAD_INTS = ["0", "201", "-1", "1e1", "4.0", "+4", " 4", "4 ", "abc", "", "0x10", "\u0664",
            "99999999999999999999999"]


@pytest.mark.parametrize("path", ["/requests", "/activity"])
@pytest.mark.parametrize("value", BAD_INTS, ids=[repr(v) for v in BAD_INTS])
def test_bad_limit(world, path, value):
    """[§5] limit integer 1..200 in plain digits."""
    expect_error(world.bob.get(path, params={"limit": value}), 422, "validation_failed")


BAD_OFFSETS = ["-1", "1.0", "+0", "1e2", "x", "", " 1"]


@pytest.mark.parametrize("path", ["/requests", "/activity"])
@pytest.mark.parametrize("value", BAD_OFFSETS, ids=[repr(v) for v in BAD_OFFSETS])
def test_bad_offset(world, path, value):
    expect_error(world.bob.get(path, params={"offset": value}), 422, "validation_failed")


def test_pagination_limits_and_has_more(world):
    """[§8] limit default 50, max 200; has_more exactly when items remain."""
    ids = [_rq(world, amount=i + 1)["request_id"] for i in range(55)]
    newest = list(reversed(ids))
    body = expect(world.bob.get("/requests"), 200).json()
    assert [r["request_id"] for r in body["requests"]] == newest[:50] and body["has_more"]
    body = expect(world.bob.get("/requests", params={"offset": "50"}), 200).json()
    assert [r["request_id"] for r in body["requests"]] == newest[50:]
    assert body["has_more"] is False
    body = expect(world.bob.get("/requests", params={"limit": "5", "offset": "50"}), 200).json()
    assert len(body["requests"]) == 5 and body["has_more"] is False
    body = expect(world.bob.get("/requests", params={"limit": "4", "offset": "50"}), 200).json()
    assert len(body["requests"]) == 4 and body["has_more"] is True
    body = expect(world.bob.get("/requests", params={"limit": "200"}), 200).json()
    assert len(body["requests"]) == 55 and body["has_more"] is False
    body = expect(world.bob.get("/requests", params={"limit": "1", "offset": "1000"}), 200).json()
    assert body == {"requests": [], "has_more": False}
    body = expect(world.bob.get("/requests", params={"limit": "007"}), 200).json()
    assert len(body["requests"]) == 7


def test_paid_request_lists_payment_id_for_both_parties(world):
    r = _rq(world, amount=5)
    p = expect(world.ada.pay_request(r["request_id"]), 201).json()
    for who in ("ada", "bob"):
        got = world.clients[who].requests_list(status="paid")
        assert [(x["request_id"], x["payment_id"]) for x in got] == \
            [(r["request_id"], p["payment_id"])]


def test_concurrent_pays_of_one_request_move_money_once(world):
    """[§1.3] adversarial: 50 different keys race to pay one request: exactly one 201."""
    r = _rq(world, amount=1000)
    clients = [world.new_client("ada") for _ in range(50)]
    out = m.burst(lambda i: clients[i].pay_request(r["request_id"]), 50)
    m.assert_no_5xx(out)
    assert m.tally(out) == {201: 1, 409: 49}, m.tally(out)
    for x in out:
        if x.status_code == 409:
            expect_error(x, 409, "request_not_pending")
    assert world.ada.balance() == 9_000
    assert len(world.bob.feed()) == 1
    world.oracle()


def test_concurrent_pay_versus_cancel_and_decline(world):
    """[§1.3, §4] racing pay/cancel/decline: one terminal state, money iff paid."""
    for _ in range(5):
        r = _rq(world, amount=100)
        payers = [world.new_client("ada") for _ in range(4)]
        cancellers = [world.new_client("bob") for _ in range(3)]

        def go(i):
            if i < 4:
                return payers[i].pay_request(r["request_id"])
            if i < 7:
                return cancellers[i - 4].post(f"/requests/{r['request_id']}/cancel", json={})
            return payers[i - 7].post(f"/requests/{r['request_id']}/decline", json={})

        before = world.ada.balance()
        out = m.burst(go, 10)
        m.assert_no_5xx(out)
        final = next(x for x in world.bob.requests_list() if x["request_id"] == r["request_id"])
        paid = [x for x in out[:4] if x.status_code == 201]
        assert final["status"] in ("paid", "cancelled", "declined")
        assert len(paid) == (1 if final["status"] == "paid" else 0), (final, m.tally(out))
        assert world.ada.balance() == before - (100 if final["status"] == "paid" else 0)
        ok_cancel = [x for x in out[4:7] if x.status_code == 200]
        ok_decline = [x for x in out[7:] if x.status_code == 200]
        if final["status"] == "paid":
            assert not ok_cancel and not ok_decline
        if final["status"] == "cancelled":
            assert not ok_decline
        if final["status"] == "declined":
            assert not ok_cancel
    world.oracle()
