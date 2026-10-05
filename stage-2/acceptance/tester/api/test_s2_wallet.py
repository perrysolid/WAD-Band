"""S2-R1 GET /me with holds; S2-R10 every insufficient_funds against `available`;
S2-R12 every payment carries authorization_id; earlier behaviour unchanged."""
from __future__ import annotations

import pf_model as m
from conftest import check_me
from pf_client import expect, expect_error


def T(frm, to, amount, **extra):
    return {"from_handle": frm, "to_handle": to, "amount": amount, **extra}


def test_me_without_holds_keeps_stage1_fields_and_agrees(world):
    """[S2-R1] balance == total == available, held 0, stage-1 fields unchanged."""
    me = world.ada.me()
    assert me == {"user_id": "u_ada", "display_name": "Ada", "handle": "ada",
                  "balance": 10_000, "total": 10_000, "available": 10_000, "held": 0,
                  "currency": "EUR", "minor_units": 2}


def test_a_hold_reserves_without_moving(world):
    """[S2-R1, S2 §1] a hold moves no money: total unchanged, available falls, held rises."""
    expect(world.ada.authorize("bob", 2000), 201)
    ada, bob = world.ada.me(), world.bob.me()
    check_me(ada), check_me(bob)
    assert (ada["total"], ada["available"], ada["held"]) == (10_000, 8_000, 2_000)
    assert ada["balance"] == 10_000
    assert (bob["total"], bob["available"], bob["held"]) == (2_500, 2_500, 0), \
        "an incoming hold is not the receiver's money until captured"
    world.oracle()


def test_held_sums_every_open_hold(world):
    for amt in (100, 250, 1):
        expect(world.ada.authorize("bob", amt), 201)
    expect(world.ada.authorize("cy", 49), 201)
    me = world.ada.me()
    assert me["held"] == 400 and me["available"] == 9_600 and me["total"] == 10_000
    world.oracle()


def test_payment_is_judged_against_available(world):
    """[S2-R10] POST /payments 409 insufficient_funds when available < amount."""
    expect(world.ada.authorize("bob", 9_000), 201)
    before = world.oracle()
    expect_error(world.ada.pay("cy", 1_001), 409, "insufficient_funds")
    assert world.oracle() == before
    expect(world.ada.pay("cy", 1_000), 201)
    expect_error(world.ada.pay("cy", 1), 409, "insufficient_funds")
    me = world.ada.me()
    assert (me["total"], me["available"], me["held"]) == (9_000, 0, 9_000)
    world.oracle()


def test_held_funds_cannot_fund_a_new_hold(world):
    """[S2 §2] held funds cannot fund new authorizations."""
    expect(world.cy.authorize("bob", 400), 201)
    expect_error(world.cy.authorize("bob", 101), 409, "insufficient_funds")
    expect(world.cy.authorize("bob", 100), 201)
    expect_error(world.cy.authorize("bob", 1), 409, "insufficient_funds")
    world.oracle()


def test_request_pay_is_judged_against_available(world):
    """[S2-R10] POST /requests/{id}/pay against available; request stays payable."""
    rid = expect(world.bob.ask("cy", 300), 201).json()["request_id"]
    aid = expect(world.cy.authorize("ada", 300), 201).json()["authorization_id"]
    expect_error(world.cy.pay_request(rid, {}), 409, "insufficient_funds")
    assert world.bob.requests_list()[0]["status"] == "pending"
    expect(world.cy.void(aid), 200)
    expect(world.cy.pay_request(rid, {}), 201)
    world.oracle()


def test_settlement_net_debit_is_judged_against_available(op_world):
    """[S2-R10] settlements: every wallet's available + net >= 0."""
    w = op_world
    expect(w.bob.authorize("cy", 2_000), 201)          # bob: total 2500, available 500
    before = w.oracle()
    expect_error(w.ada.settle([T("bob", "dee", 501)]), 409, "insufficient_funds")
    assert w.oracle() == before
    # a net debit of exactly available is affordable; incoming transfers count
    expect(w.ada.settle([T("bob", "dee", 600), T("dee", "bob", 100)]), 201)
    me = w.bob.me()
    assert (me["total"], me["available"], me["held"]) == (2_000, 0, 2_000)
    expect_error(w.ada.settle([T("bob", "dee", 1)]), 409, "insufficient_funds")
    w.oracle()


def test_settlement_cannot_spend_a_wallets_held_funds_even_transiently(op_world):
    """[S2-R10] a batch whose net debit for one wallet exceeds available is refused."""
    w = op_world
    expect(w.cy.authorize("bob", 500), 201)              # cy available 0
    before = w.oracle()
    expect_error(w.ada.settle([T("cy", "dee", 1)]), 409, "insufficient_funds")
    assert w.oracle() == before
    # net zero for cy is affordable (available 0 + net 0 >= 0)
    expect(w.ada.settle([T("ada", "cy", 5), T("cy", "dee", 5)]), 201)
    expect_error(w.ada.settle([T("ada", "cy", 5), T("cy", "dee", 6)]), 409,
                 "insufficient_funds")
    w.oracle()


def test_receiver_cannot_spend_an_incoming_hold(world):
    """[S2 §1] a hold is not a transfer: Cy (500) authorised 2000 by Ada still has 500."""
    expect(world.ada.authorize("cy", 2_000), 201)
    expect_error(world.cy.pay("bob", 501), 409, "insufficient_funds")
    expect(world.cy.pay("bob", 500), 201)
    world.oracle()


def test_request_creation_and_splits_ignore_holds(world):
    """[S2] creating a request or split never checks funds, holds or not."""
    expect(world.cy.authorize("bob", 500), 201)
    expect(world.bob.ask("cy", 99_999), 201)
    s = expect(world.cy.split(3_000, ["cy", "ada", "bob"]), 201).json()
    assert [x["amount"] for x in s["shares"]] == [1_000, 1_000, 1_000]
    world.oracle()


def test_direct_payment_is_immediate_and_leaves_no_hold(world):
    """[S2] POST /payments is an immediate transfer, never an intermediate hold."""
    p = expect(world.ada.pay("bob", 700), 201).json()
    assert p["authorization_id"] is None and p["request_id"] is None
    assert world.ada.me()["held"] == 0 and world.ada.me()["available"] == 9_300
    assert world.bob.me()["available"] == 3_200
    assert world.ada.auths() == [] and world.bob.auths() == []
    world.oracle()


def test_every_payment_carries_authorization_id_null_unless_captured(op_world):
    """[S2-R12] authorization_id null on direct, request and settlement payments."""
    w = op_world
    direct = expect(w.ada.pay("bob", 1), 201).json()
    rid = expect(w.bob.ask("ada", 2), 201).json()["request_id"]
    via_rq = expect(w.ada.pay_request(rid, {}), 201).json()
    st = expect(w.ada.settle([T("bob", "cy", 3)]), 201).json()
    for p in (direct, via_rq, *st["payments"]):
        assert "authorization_id" in p and p["authorization_id"] is None, p
    assert via_rq["request_id"] == rid
    for p in w.cy.feed():
        assert p["authorization_id"] is None
    w.oracle()


def test_paid_request_and_its_payment_are_unchanged_by_holds(world):
    """[S2] paying a request remains immediate; the request carries the payment id."""
    expect(world.ada.authorize("cy", 5_000), 201)
    rid = expect(world.bob.ask("ada", 5_000), 201).json()["request_id"]
    pay = expect(world.ada.pay_request(rid, {"visibility": "private"}), 201).json()
    assert world.bob.requests_list()[0]["payment_id"] == pay["payment_id"]
    me = world.ada.me()
    assert (me["total"], me["available"], me["held"]) == (5_000, 0, 5_000)
    world.oracle()


def test_minor_units_zero_and_three_services_report_holds(make_world):
    """[S2-R1] the new fields are plain minor-unit integers in any currency."""
    for cur in ("JPY", "BHD"):
        w = make_world(m.fixture(currency=cur))
        expect(w.ada.authorize("bob", 1_234), 201)
        me = w.ada.me()
        assert me["minor_units"] == m.MINOR_UNITS[cur] and me["currency"] == cur
        assert (me["held"], me["available"]) == (1_234, 10_000 - 1_234)
        w.oracle()
