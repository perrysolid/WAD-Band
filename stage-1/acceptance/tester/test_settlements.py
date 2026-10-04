"""§11 atomic net settlements."""
from __future__ import annotations

from datetime import datetime

import pytest

import pf_model as m
from pf_client import expect, expect_error, new_key


def T(frm, to, amount, **extra):
    return {"from_handle": frm, "to_handle": to, "amount": amount, **extra}


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def test_settlement_shape(op_world):
    """[§11] 201 settlement_id, committed_at, payments in input order with settlement_id."""
    w = op_world
    s = expect(w.ada.settle([T("bob", "cy", 100, note="a"),
                             T("cy", "dee", 50, visibility="private")]), 201).json()
    assert {"settlement_id", "committed_at", "payments"} <= set(s)
    ps = s["payments"]
    assert [(p["from_handle"], p["to_handle"], p["amount"], p["note"], p["visibility"])
            for p in ps] == [("bob", "cy", 100, "a", "public"), ("cy", "dee", 50, "", "private")]
    for p in ps:
        assert p["settlement_id"] == s["settlement_id"] and p["request_id"] is None
        assert p["created_at"] == s["committed_at"]
        assert p["currency"] == "EUR"
    assert len({p["payment_id"] for p in ps}) == 2
    assert w.balances() == {"ada": 10_000, "bob": 2_400, "cy": 550, "dee": 50}
    w.oracle()


def test_non_members_expose_null_settlement_id(op_world):
    p = expect(op_world.ada.pay("bob", 1), 201).json()
    assert "settlement_id" in p and p["settlement_id"] is None
    r = expect(op_world.bob.ask("ada", 1), 201).json()
    q = expect(op_world.ada.pay_request(r["request_id"]), 201).json()
    assert q["settlement_id"] is None
    assert all(x["settlement_id"] is None for x in op_world.ada.feed())


def test_netting_makes_a_batch_affordable(op_world):
    """[§11] affordable when every wallet's net result is non-negative, in any order."""
    w = op_world
    # dee holds 0 and sends 300 first, receiving 300 later in the batch
    s = expect(w.ada.settle([T("dee", "cy", 300), T("bob", "dee", 300)]), 201).json()
    assert len(s["payments"]) == 2
    assert w.balances() == {"ada": 10_000, "bob": 2_200, "cy": 800, "dee": 0}
    w.oracle()


def test_cycle_through_an_empty_wallet(op_world):
    w = op_world
    expect(w.ada.settle([T("dee", "bob", 1000), T("bob", "cy", 1000), T("cy", "dee", 1000)]), 201)
    assert w.balances()["dee"] == 0
    w.oracle()


def test_collective_shortfall_is_409_and_changes_nothing(op_world):
    """[§11] insufficient collective funds -> 409; all-or-nothing."""
    w = op_world
    before = w.oracle()
    expect_error(w.ada.settle([T("bob", "cy", 100), T("dee", "cy", 301), T("bob", "dee", 300)]),
                 409, "insufficient_funds")
    assert w.oracle() == before
    assert all(c.feed() == [] for c in w.clients.values())


def test_exact_net_zero_boundary(op_world):
    w = op_world
    expect(w.ada.settle([T("cy", "bob", 500)]), 201)
    assert w.cy.balance() == 0
    expect_error(w.ada.settle([T("cy", "bob", 1)]), 409, "insufficient_funds")


def test_operator_need_not_be_a_party_and_gains_no_access(op_world):
    """[§11] operators settle any wallets but see no extra requests or private items."""
    w = op_world
    r = expect(w.bob.ask("cy", 10), 201).json()
    s = expect(w.ada.settle([T("bob", "cy", 10, visibility="private")]), 201).json()
    assert w.ada.feed() == [], "operator does not see private members it is not party to"
    assert w.ada.requests_list() == []
    expect_error(w.ada.pay_request(r["request_id"]), 403, "forbidden")
    expect_error(w.ada.post(f"/requests/{r['request_id']}/cancel", json={}), 403, "forbidden")
    assert [p["payment_id"] for p in w.bob.feed()] == [s["payments"][0]["payment_id"]]
    assert w.dee.feed() == []


def test_members_follow_feed_visibility(op_world):
    w = op_world
    s = expect(w.ada.settle([T("bob", "cy", 1), T("bob", "cy", 2, visibility="private")]),
               201).json()
    pub, priv = s["payments"]
    assert w.dee.feed() == [pub]
    assert {p["payment_id"] for p in w.bob.feed()} == {pub["payment_id"], priv["payment_id"]}
    got = {p["payment_id"]: p for p in w.cy.feed()}
    assert got[pub["payment_id"]] == pub and got[priv["payment_id"]] == priv


def test_no_token_is_401_non_operator_is_403(op_world, api):
    w = op_world
    body = {"transfers": [T("bob", "cy", 1)]}
    expect_error(api().post("/settlements", json=body, token=None, key=new_key()),
                 401, "unauthenticated")
    for who in ("bob", "cy", "dee"):
        expect_error(w.clients[who].post("/settlements", json=body, key=new_key()),
                     403, "forbidden")
    w.oracle()


def test_non_operator_forbidden_before_body_validation(op_world):
    """[§11, DECISION Q5] role check right after authentication."""
    expect_error(op_world.bob.settle([]), 403, "forbidden")
    expect_error(op_world.bob.settle([T("bob", "bob", 0)]), 403, "forbidden")


def test_settlement_requires_key(op_world):
    expect_error(op_world.ada.post("/settlements", json={"transfers": [T("bob", "cy", 1)]}),
                 400, "missing_idempotency_key")


@pytest.mark.parametrize("n,status", [(1, 201), (32, 201), (33, 422), (0, 422)])
def test_batch_size_bounds(op_world, n, status):
    """[§11] transfers contains 1..32 objects."""
    resp = op_world.ada.settle([T("ada", "bob", 1)] * n)
    if status == 201:
        assert len(expect(resp, 201).json()["payments"]) == n
        assert op_world.ada.balance() == 10_000 - n
    else:
        expect_error(resp, 422, "validation_failed")
        assert op_world.ada.balance() == 10_000


@pytest.mark.parametrize("transfers", [None, "x", 5, {"from_handle": "bob"}, [5], ["x"],
                                       [None], [[T("bob", "cy", 1)]]])
def test_malformed_batch_shape_is_422(op_world, transfers):
    """[§11] malformed batch shape is 422 validation_failed."""
    expect_error(op_world.ada.settle(transfers), 422, "validation_failed")


def test_missing_transfers_is_422(op_world):
    expect_error(op_world.ada.post("/settlements", json={}, key=new_key()),
                 422, "validation_failed")


@pytest.mark.parametrize("bad,code", [
    (T("bob", "cy", 0), (422, "validation_failed")),
    (T("bob", "cy", 1.5), (422, "validation_failed")),
    (T("bob", "cy", "1"), (422, "validation_failed")),
    (T("bob", "cy", 1_000_000_001), (422, "validation_failed")),
    (T("bob", "cy", 1, note="n" * 201), (422, "validation_failed")),
    (T("bob", "cy", 1, note=None), (422, "validation_failed")),
    (T("bob", "cy", 1, visibility="hidden"), (422, "validation_failed")),
    (T("bob", "bob", 1), (422, "self_payment")),
    (T("bob", "nobody", 1), (404, "not_found")),
    (T("nobody", "bob", 1), (404, "not_found")),
    ({"to_handle": "bob", "amount": 1}, (422, "validation_failed")),
    ({"from_handle": "bob", "amount": 1}, (422, "validation_failed")),
])
def test_entry_rules(op_world, bad, code):
    """[§11] ordinary payment rules per entry; one bad entry fails the whole batch."""
    w = op_world
    before = w.oracle()
    expect_error(w.ada.settle([T("ada", "bob", 5), bad, T("ada", "cy", 5)]), *code)
    assert w.oracle() == before


def test_entry_amount_integral_forms(op_world):
    s = expect(op_world.ada.settle([T("ada", "bob", 1e2), T("ada", "bob", 3.0)]), 201).json()
    assert [p["amount"] for p in s["payments"]] == [100, 3]


def test_entry_errors_in_input_order(op_world):
    """[§11] entry errors take precedence in input order."""
    w = op_world
    expect_error(w.ada.settle([T("bob", "nobody", 1), T("bob", "bob", 1)]), 404, "not_found")
    expect_error(w.ada.settle([T("bob", "bob", 1), T("bob", "nobody", 1)]), 422, "self_payment")
    expect_error(w.ada.settle([T("bob", "cy", 0), T("bob", "nobody", 1)]),
                 422, "validation_failed")
    expect_error(w.ada.settle([T("bob", "nobody", 1), T("bob", "cy", 0)]), 404, "not_found")


def test_entry_errors_before_insufficient_funds(op_world):
    w = op_world
    expect_error(w.ada.settle([T("dee", "bob", 999), T("bob", "nobody", 1)]), 404, "not_found")
    expect_error(w.ada.settle([T("dee", "bob", 999), T("cy", "cy", 1)]), 422, "self_payment")
    expect_error(w.ada.settle([T("dee", "bob", 999), T("cy", "bob", 1, note=5)]),
                 422, "validation_failed")


def test_unknown_fields_ignored_in_batch_and_entries(op_world):
    s = expect(op_world.ada.post("/settlements", json={
        "transfers": [{**T("bob", "cy", 1), "zzz": 1, "settlement_id": "x", "created_at": "x"}],
        "extra": True}, key=new_key()), 201).json()
    assert s["payments"][0]["settlement_id"] == s["settlement_id"] != "x"


def test_settlement_replay_and_reuse(op_world):
    w = op_world
    key = new_key()
    body = [T("bob", "cy", 10), T("cy", "dee", 5)]
    first = expect(w.ada.settle(body, key=key), 201).json()
    expect(w.bob.pay("ada", 2_390), 201)  # later state change
    assert expect(w.ada.settle(body, key=key), 200).json() == first
    expect_error(w.ada.settle(body[:1], key=key), 409, "idempotency_key_reuse")
    expect_error(w.ada.settle([], key=key), 409, "idempotency_key_reuse")
    assert w.balances() == {"ada": 12_390, "bob": 100, "cy": 505, "dee": 5}
    w.oracle()


def test_committed_at_is_server_assigned_and_recent(op_world):
    s = expect(op_world.ada.settle([T("ada", "bob", 1)]), 201).json()
    p = expect(op_world.ada.pay("bob", 1), 201).json()
    assert _parse(s["committed_at"]) <= _parse(p["created_at"])


def test_operator_ids_default_empty(world):
    """[§11] settlement_operator_ids defaults to []: nobody is an operator."""
    for c in world.clients.values():
        expect_error(c.settle([T("ada", "bob", 1)]), 403, "forbidden")


def test_operator_permission_follows_the_last_reset(make_world):
    w = make_world(m.fixture(operators=["u_bob"]))
    expect(w.bob.settle([T("ada", "cy", 1)]), 201)
    expect_error(w.ada.settle([T("bob", "cy", 1)]), 403, "forbidden")
    w2 = make_world(m.fixture(operators=[]))
    expect_error(w2.bob.settle([T("ada", "cy", 1)]), 403, "forbidden")


def test_concurrent_settlements_cannot_overdraw(op_world):
    """[§1.2, §11] adversarial: 30 batches each moving cy's whole 500 race: one wins."""
    w = op_world
    clients = [w.new_client("ada") for _ in range(30)]
    out = m.burst(lambda i: clients[i].settle([T("cy", "dee", 250), T("cy", "bob", 250)]), 30)
    m.assert_no_5xx(out)
    assert m.tally(out) == {201: 1, 409: 29}, m.tally(out)
    assert w.cy.balance() == 0
    w.oracle()


def test_settlements_race_payments_on_the_same_wallet(op_world):
    """[§1.2, §11] payments and settlements draining cy at once never overdraw."""
    w = op_world
    ops = [w.new_client("ada") for _ in range(10)]
    cys = [w.new_client("cy") for _ in range(20)]

    def go(i):
        if i < 10:
            return ops[i].settle([T("cy", "dee", 60), T("dee", "bob", 10)])
        return cys[i - 10].pay("bob", 60)

    out = m.burst(go, 30)
    m.assert_no_5xx(out)
    wins = sum(1 for r in out if r.status_code == 201)
    assert wins == 8, f"500 // 60 = 8 debits of 60 fit: {m.tally(out)}"
    assert w.cy.balance() == 500 - 60 * 8
    w.oracle()
