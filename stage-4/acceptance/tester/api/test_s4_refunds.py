"""Refunds (stage-4 'Refunds and corrected history'). Error order follows S4-D1:
body 400, 401, key 400, claimed key, amount 422, 404, 403 non-receiver, 422 invalid_refund_target,
422 refund_exceeds_payment, 409 insufficient_funds, 201."""
from __future__ import annotations

import pytest

import pf_model as m
from pf_client import expect, expect_error, expect_one_of, new_key


def pay(w, frm, to, amount, **kw):
    return expect(w.clients[frm].pay(to, amount, **kw), 201).json()


def test_refund_is_a_reverse_payment_with_refund_of(world):
    p = pay(world, "ada", "bob", 500, note="café ☕", visibility="private")
    r = expect(world.bob.refund(p["payment_id"], 200), 201).json()
    assert r["refund_of"] == p["payment_id"] and r["amount"] == 200
    assert (r["from_handle"], r["to_handle"]) == ("bob", "ada")
    assert (r["from_user_id"], r["to_user_id"]) == (p["to_user_id"], p["from_user_id"])
    assert r["request_id"] is None and r["authorization_id"] is None and r["settlement_id"] is None
    assert r["note"] == "café ☕" and r["visibility"] == "private" and r["currency"] == "EUR"
    assert r["payment_id"] != p["payment_id"]
    m.assert_d35(r["created_at"])
    assert world.ada.balance() == 10_000 - 500 + 200 and world.bob.balance() == 2_500 + 500 - 200
    assert p["refund_of"] is None
    world.oracle()


def test_refund_is_a_feed_payment_by_the_usual_rule(world):
    pub = pay(world, "ada", "bob", 100)
    priv = pay(world, "ada", "bob", 100, visibility="private")
    rp = expect(world.bob.refund(pub["payment_id"], 10), 201).json()
    rv = expect(world.bob.refund(priv["payment_id"], 10), 201).json()
    cy_ids = {p["payment_id"] for p in world.cy.feed()}
    assert rp["payment_id"] in cy_ids and rv["payment_id"] not in cy_ids
    for who in (world.ada, world.bob):
        assert {rp["payment_id"], rv["payment_id"]} <= {p["payment_id"] for p in who.feed()}
    feed = {p["payment_id"]: p for p in world.ada.feed()}
    assert feed[rp["payment_id"]]["refund_of"] == pub["payment_id"]
    assert feed[pub["payment_id"]]["refund_of"] is None


def test_every_payment_shape_carries_refund_of(op_world):
    w = op_world
    p = pay(w, "ada", "bob", 10)
    a = expect(w.ada.authorize("bob", 20), 201).json()
    c = expect(w.bob.capture(a["authorization_id"]), 201).json()
    s = expect(w.ada.settle([{"from_handle": "ada", "to_handle": "cy", "amount": 1}]), 201).json()
    rq = expect(w.bob.ask("ada", 3), 201).json()
    rp = expect(w.ada.pay_request(rq["request_id"]), 201).json()
    shapes = [p, c, rp, *s["payments"], *w.ada.feed(), *[e["payment"] for e in w.ada.statement()["entries"]]]
    for x in shapes:
        assert "refund_of" in x and x["refund_of"] is None, x


def test_refund_in_statements_and_revisions(world):
    p = pay(world, "ada", "bob", 500)
    r = expect(world.bob.refund(p["payment_id"], 120), 201).json()
    ea = [e for e in world.ada.statement()["entries"] if e["payment"]["payment_id"] == r["payment_id"]][0]
    eb = [e for e in world.bob.statement()["entries"] if e["payment"]["payment_id"] == r["payment_id"]][0]
    assert ea["delta"] == 120 and eb["delta"] == -120 and ea["revision"] == 1
    assert ea["payment"]["refund_of"] == p["payment_id"]
    revs = world.bob.revisions(r["payment_id"])
    assert len(revs) == 1 and revs[0]["reason"] == "" and revs[0]["amount"] == 120
    assert m.parse(revs[0]["effective_at"]) == m.parse(revs[0]["recorded_at"]) == m.parse(r["created_at"])
    world.oracle()


def test_cumulative_refunds_are_bounded_by_the_payment(world):
    p = pay(world, "ada", "bob", 500)
    pid = p["payment_id"]
    expect(world.bob.refund(pid, 200), 201)
    expect(world.bob.refund(pid, 299), 201)
    expect_error(world.bob.refund(pid, 2), 422, "refund_exceeds_payment")
    expect(world.bob.refund(pid, 1), 201)
    expect_error(world.bob.refund(pid, 1), 422, "refund_exceeds_payment")
    assert world.ada.balance() == 10_000 and world.bob.balance() == 2_500
    world.oracle()


def test_a_single_refund_above_the_payment(world):
    p = pay(world, "ada", "bob", 500)
    expect_error(world.bob.refund(p["payment_id"], 501), 422, "refund_exceeds_payment")
    expect(world.bob.refund(p["payment_id"], 500), 201)


def test_refund_bound_follows_the_current_corrected_amount(world):
    p = pay(world, "ada", "bob", 500)
    pid, c = p["payment_id"], p["created_at"]
    expect(world.ada.correct(pid, amount=300, effective_at=c), 201)
    expect_error(world.bob.refund(pid, 301), 422, "refund_exceeds_payment")
    expect(world.bob.refund(pid, 300), 201)
    expect(world.ada.correct(pid, expected_revision=2, amount=800, effective_at=c), 201)
    expect(world.bob.refund(pid, 500), 201)                      # 300 + 500 = 800
    expect_error(world.bob.refund(pid, 1), 422, "refund_exceeds_payment")
    world.oracle()


def test_a_correction_cannot_reduce_a_payment_below_its_refunded_amount(world):
    p = pay(world, "ada", "bob", 500)
    pid, c = p["payment_id"], p["created_at"]
    expect(world.bob.refund(pid, 200), 201)
    before = (world.balances(), world.ada.revisions(pid))
    k = new_key()
    expect_error(world.ada.correct(pid, amount=199, effective_at=c, key=k), 422, "refund_exceeds_payment")
    expect_error(world.ada.correct(pid, amount=0, effective_at=c), 422, "refund_exceeds_payment")
    assert (world.balances(), world.ada.revisions(pid)) == before
    expect(world.ada.correct(pid, amount=200, effective_at=c, key=k), 201)   # exactly refunded
    world.oracle()


def test_correction_order_stale_then_refund_exceeds(world):
    p = pay(world, "ada", "bob", 500)
    expect(world.bob.refund(p["payment_id"], 400), 201)
    expect_error(world.ada.correct(p["payment_id"], expected_revision=5, amount=1,
                                   effective_at=p["created_at"]), 409, "stale_revision")


@pytest.mark.parametrize("amt", [0, -1, 1.5, "5", True, None, 1_000_000_001, [1], {"a": 1}])
def test_invalid_refund_amounts_are_422(world, amt):
    p = pay(world, "ada", "bob", 500)
    expect_error(world.bob.refund(p["payment_id"], amt), 422, "validation_failed")
    assert world.bob.balance() == 3_000


def test_missing_amount_is_422_and_integral_forms_are_accepted(world):
    p = pay(world, "ada", "bob", 500)
    expect_error(world.bob.post(f"/payments/{p['payment_id']}/refunds", json={}, key=new_key()), 422,
                 "validation_failed")
    expect(world.bob.post(f"/payments/{p['payment_id']}/refunds", content='{"amount": 1e2}',
                          key=new_key()), 201)
    expect(world.bob.post(f"/payments/{p['payment_id']}/refunds", content='{"amount": 100.0}',
                          key=new_key()), 201)


def test_body_must_be_json(world):
    p = pay(world, "ada", "bob", 500)
    for raw in ("{", "", "not json"):
        expect_error(world.bob.post(f"/payments/{p['payment_id']}/refunds", content=raw,
                                    key=new_key()), 400, "malformed_request")
    for raw in ("[]", "5", "null"):
        expect_one_of(world.bob.post(f"/payments/{p['payment_id']}/refunds", content=raw,
                                     key=new_key()), (400, "malformed_request"), (422, "validation_failed"))


def test_only_the_receiver_may_refund(world):
    p = pay(world, "ada", "bob", 500)
    expect_error(world.ada.refund(p["payment_id"], 1), 403, "forbidden")      # the sender
    expect_error(world.cy.refund(p["payment_id"], 1), 403, "forbidden")        # a third party
    expect_error(world.cy.refund(p["payment_id"], 0), 422, "validation_failed")  # amount first
    expect_error(world.bob.refund("p_nope", 1), 404, "not_found")
    expect_error(world.bob.refund("p_nope", 0), 422, "validation_failed")
    expect_error(world.cy.refund(p["payment_id"], 10_000), 403, "forbidden")  # before exceeds
    assert world.bob.balance() == 3_000


def test_auth_and_key_rules(world, api):
    p = pay(world, "ada", "bob", 500)
    url = f"/payments/{p['payment_id']}/refunds"
    expect_error(api().post(url, json={"amount": 1}, key=new_key()), 401, "unauthenticated")
    expect_error(world.bob.post(url, json={"amount": 1}), 400, "missing_idempotency_key")
    expect_error(world.bob.post(url, json={"amount": 1}, key=""), 400, "missing_idempotency_key")
    expect_error(world.bob.post(url, json={"amount": 1}, key="k" * 256), 422, "validation_failed")
    expect(world.bob.post(url, json={"amount": 1}, key="k" * 255), 201)


def test_refund_of_a_refund_is_invalid(world):
    p = pay(world, "ada", "bob", 500)
    r = expect(world.bob.refund(p["payment_id"], 100), 201).json()
    expect_error(world.ada.refund(r["payment_id"], 10), 422, "invalid_refund_target")
    expect_error(world.ada.refund(r["payment_id"], 10_000), 422, "invalid_refund_target")   # before exceeds
    expect_error(world.bob.refund(r["payment_id"], 10), 403, "forbidden")
    expect_error(world.cy.refund(r["payment_id"], 10), 403, "forbidden")
    expect_error(world.ada.refund(r["payment_id"], 0), 422, "validation_failed")


def test_refund_payments_cannot_be_corrected(world):
    p = pay(world, "ada", "bob", 500)
    r = expect(world.bob.refund(p["payment_id"], 100), 201).json()
    expect_error(world.bob.correct(r["payment_id"], amount=1, effective_at=r["created_at"]), 422,
                 "linked_payment_immutable")
    expect_error(world.ada.correct(r["payment_id"], amount=1), 403, "forbidden")
    assert len(world.bob.revisions(r["payment_id"])) == 1


def test_exceeds_comes_before_insufficient_funds(world):
    p = pay(world, "ada", "bob", 500)
    pay(world, "bob", "cy", 3_000)                                   # bob now holds 0
    assert world.bob.balance() == 0
    expect_error(world.bob.refund(p["payment_id"], 501), 422, "refund_exceeds_payment")
    expect_error(world.bob.refund(p["payment_id"], 100), 409, "insufficient_funds")


def test_refund_funds_are_the_receivers_available_not_total(world):
    p = pay(world, "ada", "bob", 500)
    expect(world.bob.authorize("cy", 3_000), 201)                     # bob total 3000, available 0
    before = world.wallets()
    k = new_key()
    expect_error(world.bob.refund(p["payment_id"], 10, key=k), 409, "insufficient_funds")
    assert world.wallets() == before and world.ada.feed()[0]["refund_of"] is None
    expect(world.bob.void(world.bob.auths(direction="outgoing")[0]["authorization_id"]), 200)
    expect(world.bob.refund(p["payment_id"], 10, key=k), 201)         # failed key is reusable
    world.oracle()


def test_a_failed_refund_changes_nothing(world):
    p = pay(world, "ada", "bob", 500)
    pay(world, "bob", "cy", 3_000)
    before = (world.balances(), len(world.ada.feed()), len(world.bob.statement_all()["entries"]))
    expect_error(world.bob.refund(p["payment_id"], 100), 409, "insufficient_funds")
    expect_error(world.bob.refund(p["payment_id"], 900), 422, "refund_exceeds_payment")
    assert (world.balances(), len(world.ada.feed()), len(world.bob.statement_all()["entries"])) == before
    pay(world, "cy", "bob", 100)
    expect(world.bob.refund(p["payment_id"], 100), 201)               # the 409 left the budget intact
    world.oracle()


def test_replay_and_key_reuse(world):
    p = pay(world, "ada", "bob", 500)
    k = new_key()
    first = expect(world.bob.refund(p["payment_id"], 100, key=k), 201).json()
    assert expect(world.bob.refund(p["payment_id"], 100, key=k), 200).json() == first
    assert world.bob.balance() == 3_000 - 100
    expect_error(world.bob.refund(p["payment_id"], 101, key=k), 409, "idempotency_key_reuse")
    expect_error(world.bob.refund(p["payment_id"], 0, key=k), 409, "idempotency_key_reuse")
    # the same key on another payment is a different request
    q = pay(world, "ada", "bob", 50)
    expect(world.bob.refund(q["payment_id"], 50, key=k), 201)
    # replay survives a later exhaustion of the budget
    expect(world.bob.refund(p["payment_id"], 400, key=new_key()), 201)
    assert expect(world.bob.refund(p["payment_id"], 100, key=k), 200).json() == first
    world.oracle()


def test_refund_targets(op_world):
    w = op_world
    # request payment
    rq = expect(w.bob.ask("ada", 300), 201).json()
    rp = expect(w.ada.pay_request(rq["request_id"]), 201).json()
    expect(w.bob.refund(rp["payment_id"], 100), 201)
    assert [x for x in w.bob.requests_list() if x["request_id"] == rq["request_id"]][0]["status"] == "paid"
    # capture
    a = expect(w.ada.authorize("bob", 400), 201).json()
    c = expect(w.bob.capture(a["authorization_id"], {"amount": 150}), 201).json()
    held_after = w.ada.me()["held"]
    r = expect(w.bob.refund(c["payment_id"], 150), 201).json()
    assert r["authorization_id"] is None and r["refund_of"] == c["payment_id"]
    st = w.ada.auth(a["authorization_id"])
    assert st["status"] == "captured" and w.ada.me()["held"] == held_after == 0
    expect_error(w.ada.get("/payments/x/revisions"), 404, "not_found")
    # settlement member: refunded by the member's receiver; membership unchanged
    s = expect(w.ada.settle([{"from_handle": "ada", "to_handle": "cy", "amount": 40},
                             {"from_handle": "bob", "to_handle": "dee", "amount": 20}]), 201).json()
    m1 = s["payments"][0]
    rf = expect(w.cy.refund(m1["payment_id"], 15), 201).json()
    assert rf["settlement_id"] is None and rf["refund_of"] == m1["payment_id"]
    members = [p["payment_id"] for p in w.ada.feed() if p["settlement_id"] == s["settlement_id"]]
    assert sorted(members) == sorted(p["payment_id"] for p in s["payments"])
    w.oracle()


def test_a_seeded_payment_can_be_refunded(hw):
    expect(hw.bob.refund("p_1", 500), 201)
    expect_error(hw.bob.refund("p_1", 1), 422, "refund_exceeds_payment")
    assert hw.ada.balance() == 10_500
    hw.oracle()


def test_refund_never_restores_a_released_hold(world):
    a = expect(world.ada.authorize("bob", 1_000), 201).json()
    c = expect(world.bob.capture(a["authorization_id"], {"amount": 300}), 201).json()   # releases 700
    expect(world.bob.refund(c["payment_id"], 300), 201)
    me = world.ada.me()
    assert (me["total"], me["held"], me["available"]) == (10_000, 0, 10_000)
    assert world.ada.auth(a["authorization_id"])["status"] == "captured"
    world.oracle()


def test_concurrent_refunds_never_exceed_the_payment(world):
    p = pay(world, "ada", "bob", 500)
    res = m.burst(lambda i: world.bob.refund(p["payment_id"], 100), 30)
    m.assert_no_5xx(res)
    ok = [r for r in res if r.status_code == 201]
    assert len(ok) == 5, m.codes(res)
    assert all(r.status_code == 422 and r.json()["error"]["code"] == "refund_exceeds_payment"
               for r in res if r.status_code != 201)
    assert world.ada.balance() == 10_000 and world.bob.balance() == 2_500
    world.oracle()


def test_concurrent_identical_refund_applies_once(world):
    p = pay(world, "ada", "bob", 500)
    k = new_key()
    res = m.burst(lambda i: world.bob.refund(p["payment_id"], 100, key=k), 30)
    m.assert_no_5xx(res)
    assert sorted(m.tally(res).items()) == [(200, 29), (201, 1)]
    assert world.bob.balance() == 3_000 - 100
    world.oracle()


def test_concurrent_refunds_and_spending_cannot_overdraw(make_world):
    users = [m.user("ada", 1_000), m.user("bob", 0), m.user("cy", 0)]
    w = make_world(m.fixture(users))
    p = pay(w, "ada", "bob", 600)
    def op(i):
        return w.bob.refund(p["payment_id"], 400) if i % 2 else w.bob.pay("cy", 400)
    res = m.burst(op, 20)
    m.assert_no_5xx(res)
    assert w.bob.balance() >= 0
    assert sum(w.balances().values()) == 1_000
    w.oracle()


def test_refund_versus_correction_race(world):
    p = pay(world, "ada", "bob", 500)
    for i in range(1):
        res = m.burst(lambda i: (world.bob.refund(p["payment_id"], 400) if i == 0 else
                                 world.ada.correct(p["payment_id"], amount=100, effective_at=p["created_at"])), 2)
    m.assert_no_5xx(res)
    # either order is fine, but the refund total never exceeds the final amount
    final = world.ada.revisions(p["payment_id"])[-1]["amount"]
    refunded = sum(x["amount"] for x in world.bob.feed() if x["refund_of"] == p["payment_id"])
    assert refunded <= final, (refunded, final)
    world.oracle()


def test_snapshots_ignore_later_refunds(hw):
    first = hw.bob.statement(limit=1)
    want = hw.bob.statement(snapshot=first["snapshot"], limit=200)["entries"]
    expect(hw.bob.refund("p_1", 100), 201)
    assert hw.bob.statement(snapshot=first["snapshot"], limit=200)["entries"] == want
    assert len(hw.bob.statement()["entries"]) == len(want) + 1


def test_fifty_mixed_writers_with_refunds_conserve_money(make_world):
    users = [m.user(h, 3_000) for h in ("ada", "bob", "cy", "dee")]
    w = make_world(m.fixture(users))
    hs = ["ada", "bob", "cy", "dee"]
    base = [expect(w.clients[h].pay(hs[(i + 1) % 4], 200), 201).json() for i, h in enumerate(hs)]

    def op(i):
        c = w.clients[hs[i % 4]]
        k = i % 5
        if k == 0:
            tgt = [b for b in base if b["to_handle"] == hs[i % 4]][0]
            return c.refund(tgt["payment_id"], 30)
        if k == 1:
            return c.pay(hs[(i + 2) % 4], 10 + i % 20)
        if k == 2:
            mine = [b for b in base if b["from_handle"] == hs[i % 4]][0]
            return c.correct(mine["payment_id"], amount=(i * 3) % 200, effective_at=mine["created_at"])
        if k == 3:
            return c.authorize(hs[(i + 1) % 4], 25)
        return c.get("/statement", params={"limit": 3})

    res = m.burst(op, 200)
    m.assert_no_5xx(res)
    for r in res:
        assert r.status_code in (200, 201, 409, 422), (r.status_code, r.text[:200])
    assert sum(w.balances().values()) == w.total
    w.oracle()
