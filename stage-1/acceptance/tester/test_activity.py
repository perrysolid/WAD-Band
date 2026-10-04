"""§4 feed contract and §8 GET /activity; ownership of resources."""
from __future__ import annotations

import random

import pytest

import pf_model as m
from pf_client import expect, expect_error


@pytest.fixture
def fw(make_world):
    return make_world(m.fixture([m.ADA, m.BOB, m.CY, m.user("dee", 1000)]))


def _ids(client) -> list[str]:
    return [p["payment_id"] for p in client.feed()]


def test_feed_contract_exactly(fw):
    """[§4] visible iff public, or the caller is sender or receiver. No other rule."""
    rng = random.Random(7)
    handles = ["ada", "bob", "cy", "dee"]
    made = []
    for _ in range(40):
        a, b = rng.sample(handles, 2)
        vis = rng.choice(["public", "private"])
        resp = fw.clients[a].pay(b, rng.randint(1, 20), visibility=vis)
        if resp.status_code == 201:
            made.append(resp.json())
    for h in handles:
        expected = {p["payment_id"] for p in made
                    if p["visibility"] == "public" or h in (p["from_handle"], p["to_handle"])}
        assert set(_ids(fw.clients[h])) == expected, h
    fw.oracle()


def test_visibility_is_one_value_seen_identically(fw):
    """[§4] both parties and third parties see the same visibility value."""
    p = expect(fw.ada.pay("bob", 1, visibility="private"), 201).json()
    q = expect(fw.ada.pay("bob", 2, visibility="public"), 201).json()
    for h in ("ada", "bob"):
        got = {x["payment_id"]: x for x in fw.clients[h].feed()}
        assert got[p["payment_id"]] == p and got[q["payment_id"]] == q
    assert fw.cy.feed() == [q] and fw.dee.feed() == [q]


def test_feed_newest_first(fw):
    """[§8] newest first by created_at (non-decreasing created_at down the list)."""
    for i in range(5):
        expect(fw.ada.pay("bob", i + 1), 201)
    feed = fw.cy.feed()
    stamps = [p["created_at"] for p in feed]
    from datetime import datetime
    parsed = [datetime.fromisoformat(s.replace("Z", "+00:00")) for s in stamps]
    assert parsed == sorted(parsed, reverse=True)


def test_feed_only_payments_never_requests_or_splits(fw):
    """[§4] requests, splits and declined/cancelled requests never appear."""
    r = expect(fw.bob.ask("ada", 10), 201).json()
    expect(fw.ada.split(30, ["ada", "bob", "cy"]), 201)
    expect(fw.ada.post(f"/requests/{r['request_id']}/decline", json={}), 200)
    for c in fw.clients.values():
        assert c.feed() == []
    r2 = expect(fw.bob.ask("ada", 10), 201).json()
    p = expect(fw.ada.pay_request(r2["request_id"], {"visibility": "public"}), 201).json()
    assert fw.dee.feed() == [p] and p["request_id"] == r2["request_id"]


def test_feed_pagination(fw):
    for i in range(12):
        expect(fw.ada.pay("bob", 1), 201)
    a = expect(fw.cy.get("/activity", params={"limit": "5"}), 200).json()
    b = expect(fw.cy.get("/activity", params={"limit": "5", "offset": "5"}), 200).json()
    c = expect(fw.cy.get("/activity", params={"limit": "5", "offset": "10"}), 200).json()
    assert (len(a["payments"]), a["has_more"]) == (5, True)
    assert (len(b["payments"]), b["has_more"]) == (5, True)
    assert (len(c["payments"]), c["has_more"]) == (2, False)
    ids = [p["payment_id"] for x in (a, b, c) for p in x["payments"]]
    assert len(set(ids)) == 12
    d = expect(fw.cy.get("/activity", params={"limit": "2", "offset": "10"}), 200).json()
    assert d["has_more"] is False
    assert expect(fw.cy.get("/activity"), 200).json()["has_more"] is False
    assert expect(fw.cy.get("/activity", params={"limit": "200", "offset": "0"}),
                  200).json()["has_more"] is False


def test_default_feed_limit_is_50(fw):
    for _ in range(51):
        expect(fw.ada.pay("bob", 1), 201)
    body = expect(fw.dee.get("/activity"), 200).json()
    assert len(body["payments"]) == 50 and body["has_more"] is True


def test_empty_feed(fw):
    assert expect(fw.dee.get("/activity"), 200).json() == {"payments": [], "has_more": False}


# ---- ownership ----------------------------------------------------------------

def test_other_users_requests_are_invisible_and_untouchable(fw):
    """[§8] GET /requests returns only the caller's; others get 403 on actions."""
    r = expect(fw.bob.ask("ada", 10), 201).json()
    assert fw.cy.requests_list() == [] and fw.dee.requests_list() == []
    for action in ("pay", "decline", "cancel"):
        resp = fw.cy.pay_request(r["request_id"]) if action == "pay" else \
            fw.cy.post(f"/requests/{r['request_id']}/{action}", json={})
        expect_error(resp, 403, "forbidden")
    assert fw.bob.requests_list()[0]["status"] == "pending"
    fw.oracle()


def test_private_payment_not_leaked_through_request_lists(fw):
    """[§4] a private request payment is hidden from third parties everywhere."""
    r = expect(fw.bob.ask("ada", 10), 201).json()
    expect(fw.ada.pay_request(r["request_id"], {"visibility": "private"}), 201)
    assert fw.cy.feed() == [] and fw.cy.requests_list() == []


def test_paying_someone_does_not_reveal_their_balance(fw):
    """[§4] payment bodies carry no balance fields."""
    p = expect(fw.ada.pay("bob", 1), 201).json()
    assert "balance" not in p
