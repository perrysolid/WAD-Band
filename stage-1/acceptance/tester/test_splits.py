"""§8 POST /splits and §9 money and rounding."""
from __future__ import annotations

import pytest

import pf_model as m
from pf_client import expect, expect_error, new_key


def _split(w, amount, handles, who="ada", **extra) -> dict:
    return expect(w.clients[who].split(amount, handles, **extra), 201).json()


@pytest.mark.parametrize("amount,n,shares", [(1000, 3, [334, 333, 333]), (1, 3, [1, 0, 0]),
                                             (10, 3, [4, 3, 3]), (999, 3, [333, 333, 333]),
                                             (5, 5, [1, 1, 1, 1, 1]), (2, 5, [1, 1, 0, 0, 0]),
                                             (1_000_000_000, 7, m.equal_split(10**9, 7))])
def test_spec_share_table(make_world, amount, n, shares):
    """[§9] the table, verbatim, plus extra boundaries."""
    handles = ["ada", "bob", "cy", "dee", "eve", "fay", "gus"][:n]
    w = make_world(m.fixture([m.user(h, 10) for h in handles]))
    s = _split(w, amount, handles)
    assert [x["amount"] for x in s["shares"]] == shares
    assert [x["handle"] for x in s["shares"]] == handles
    assert sum(x["amount"] for x in s["shares"]) == amount


def test_split_shape(world):
    """[§8 splits] split_id, amount, currency, note, shares, requests, created_at."""
    s = _split(world, 3000, ["ada", "bob", "cy"], note="dinner")
    assert {"split_id", "amount", "currency", "note", "shares", "requests", "created_at"} <= set(s)
    assert (s["amount"], s["currency"], s["note"]) == (3000, "EUR", "dinner")
    assert s["shares"] == [{"handle": "ada", "amount": 1000}, {"handle": "bob", "amount": 1000},
                           {"handle": "cy", "amount": 1000}]
    assert [(r["payer_handle"], r["requester_handle"], r["amount"], r["status"], r["note"])
            for r in s["requests"]] == [("bob", "ada", 1000, "pending", "dinner"),
                                        ("cy", "ada", 1000, "pending", "dinner")]


def test_split_note_defaults_empty(world):
    s = _split(world, 10, ["bob"])
    assert s["note"] == "" and s["requests"][0]["note"] == ""


def test_caller_may_be_omitted(world):
    """[§8] caller not listed: every listed participant gets a request."""
    s = _split(world, 1000, ["bob", "cy"])
    assert [x["amount"] for x in s["shares"]] == [500, 500]
    assert [r["payer_handle"] for r in s["requests"]] == ["bob", "cy"]


def test_caller_in_the_middle_keeps_order(world):
    s = _split(world, 10, ["bob", "ada", "cy"])
    assert s["shares"] == [{"handle": "bob", "amount": 4}, {"handle": "ada", "amount": 3},
                           {"handle": "cy", "amount": 3}]
    assert [(r["payer_handle"], r["amount"]) for r in s["requests"]] == [("bob", 4), ("cy", 3)]


def test_split_of_only_the_caller(world):
    """[§8] valid: one share, zero requests."""
    s = _split(world, 500, ["ada"])
    assert s["shares"] == [{"handle": "ada", "amount": 500}] and s["requests"] == []
    assert world.ada.requests_list() == []


def test_zero_share_still_creates_a_request(world):
    """[§9] a share of 0 is legal and still produces a request."""
    s = _split(world, 1, ["ada", "bob", "cy"])
    assert [(r["payer_handle"], r["amount"]) for r in s["requests"]] == [("bob", 0), ("cy", 0)]
    zero = s["requests"][1]
    assert [x["amount"] for x in world.cy.requests_list()] == [0]
    p = expect(world.cy.pay_request(zero["request_id"]), 201).json()
    assert p["amount"] == 0
    world.oracle()


def test_order_moves_the_extra_unit(world):
    """[§9] a different order gives the extra unit to a different person."""
    a = _split(world, 10, ["ada", "bob", "cy"])
    b = _split(world, 10, ["cy", "bob", "ada"])
    assert a["shares"][0] == {"handle": "ada", "amount": 4}
    assert b["shares"][0] == {"handle": "cy", "amount": 4}


def test_splits_are_independent(world):
    """[§9] each split's shares ignore previous splits."""
    for _ in range(3):
        assert [x["amount"] for x in _split(world, 10, ["ada", "bob", "cy"])["shares"]] == [4, 3, 3]


def test_split_checks_no_balance(make_world):
    """[§8] nothing about a split checks anyone's balance."""
    w = make_world(m.fixture([m.user("ada", 0), m.user("bob", 0), m.user("cy", 0)]))
    s = _split(w, 1_000_000_000, ["ada", "bob", "cy"])
    assert len(s["requests"]) == 2
    w.oracle()


def test_split_requests_are_visible_only_to_their_two_parties(make_world):
    w = make_world(m.fixture([m.ADA, m.BOB, m.CY, m.user("dee", 0)]))
    s = _split(w, 30, ["ada", "bob", "cy"])
    assert {r["request_id"] for r in w.ada.requests_list()} == \
        {r["request_id"] for r in s["requests"]}
    assert [r["request_id"] for r in w.bob.requests_list()] == [s["requests"][0]["request_id"]]
    assert [r["request_id"] for r in w.cy.requests_list()] == [s["requests"][1]["request_id"]]
    assert w.dee.requests_list() == []
    listed = next(r for r in w.bob.requests_list())
    assert listed == s["requests"][0]


def test_split_is_not_a_feed_item_and_paid_shares_conserve(world):
    """[§4, §9] after every split is paid in full, the total is unchanged."""
    for amount in (100, 1, 10, 99, 7, 101):
        s = _split(world, amount, ["ada", "bob", "cy"])
        for r in s["requests"]:
            expect(world.clients[r["payer_handle"]].pay_request(r["request_id"]), 201)
    world.oracle()
    for c in world.clients.values():
        assert all(p["request_id"] for p in c.feed()), "only request payments in the feed"


@pytest.mark.parametrize("amount", [0, -1, 2.5, 1_000_000_001, "10", True, None])
def test_split_amount_rules(world, amount):
    expect_error(world.ada.split(amount, ["ada", "bob"]), 422, "validation_failed")


@pytest.mark.parametrize("handles", [[], ["bob", "bob"], ["ada", "bob", "ada"]])
def test_split_participant_rules(world, handles):
    """[§8] empty, or containing a duplicate handle -> 422."""
    expect_error(world.ada.split(10, handles), 422, "validation_failed")
    assert world.ada.requests_list() == []


def test_split_note_rules(world):
    expect_error(world.ada.split(10, ["bob"], note="n" * 201), 422, "validation_failed")
    expect_error(world.ada.split(10, ["bob"], note=None), 422, "validation_failed")
    assert _split(world, 10, ["bob"], note="n" * 200)["note"] == "n" * 200


def test_split_unknown_handle_creates_nothing(world):
    """[§8] any unknown handle -> 404; no request created for the known ones."""
    expect_error(world.ada.split(30, ["bob", "nobody", "cy"]), 404, "not_found")
    assert world.ada.requests_list() == [] and world.bob.requests_list() == []


def test_split_missing_fields(world):
    expect_error(world.ada.post("/splits", json={"amount": 10}, key=new_key()),
                 422, "validation_failed")
    expect_error(world.ada.post("/splits", json={"participant_handles": ["bob"]}, key=new_key()),
                 422, "validation_failed")


@pytest.mark.parametrize("handles", ["bob", 5, {"bob": 1}, ["bob", 5], [None]])
def test_split_wrong_type_participants(world, handles):
    """[§5] participant_handles of the wrong JSON type -> 400 malformed_request."""
    expect_error(world.ada.split(10, handles), 400, "malformed_request")


def test_split_precedence(world):
    """[§5 order] amount -> participants -> note -> 404 (DECISION Q4)."""
    expect_error(world.ada.split(0, ["nobody"]), 422, "validation_failed")
    expect_error(world.ada.split(10, ["nobody", "nobody"]), 422, "validation_failed")
    expect_error(world.ada.split(10, ["nobody"], note="n" * 201), 422, "validation_failed")


def test_split_with_33_participants_in_jpy(make_world):
    """[§4, §9] zero minor units; many participants; sum exact."""
    users = [m.user(f"p{i}", 0) for i in range(33)]
    w = make_world(m.fixture(users, currency="JPY"))
    s = _split(w, 100, [u["handle"] for u in users], who="p0")
    assert [x["amount"] for x in s["shares"]] == m.equal_split(100, 33)
    assert len(s["requests"]) == 32 and s["currency"] == "JPY"


def test_split_requests_share_the_split_created_at(world):
    """[D24] requests created in participant order with the split's created_at."""
    s = _split(world, 30, ["ada", "bob", "cy"])
    assert {r["created_at"] for r in s["requests"]} == {s["created_at"]}
