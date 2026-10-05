"""Concurrency for corrections, snapshots and mixed writers; every test ends with the oracle."""
from __future__ import annotations

import random

import pytest

import pf_model as m
from pf_client import expect, new_key
from hist import at

FAR = "2999-01-01T00:00:00+00:00"


def test_concurrent_corrections_on_one_expected_revision_one_wins(world):
    p = expect(world.ada.pay("bob", 1_000), 201).json()
    pid, c = p["payment_id"], p["created_at"]
    res = m.burst(lambda i: world.ada.correct(pid, expected_revision=1, amount=100 + i,
                                              effective_at=c, reason=f"r{i}"), 30)
    m.assert_no_5xx(res)
    won = [r for r in res if r.status_code == 201]
    assert len(won) == 1, m.tally(res)
    assert all(r.status_code == 409 and r.json()["error"]["code"] == "stale_revision"
               for r in res if r.status_code != 201)
    revs = world.ada.revisions(pid)
    assert len(revs) == 2 and revs[1]["amount"] == won[0].json()["amount"]
    assert world.ada.balance() == 10_000 - revs[1]["amount"]
    world.oracle()


def test_concurrent_identical_correction_applies_once(world):
    p = expect(world.ada.pay("bob", 1_000), 201).json()
    k = new_key()
    res = m.burst(lambda i: world.ada.correct(p["payment_id"], amount=250,
                                              effective_at=p["created_at"], key=k), 30)
    m.assert_no_5xx(res)
    assert sorted(m.tally(res).items()) == [(200, 29), (201, 1)], m.tally(res)
    bodies = {tuple(sorted(r.json().items())) for r in res}
    assert len(bodies) == 1
    assert len(world.ada.revisions(p["payment_id"])) == 2
    assert world.ada.balance() == 9_750
    world.oracle()


def test_competing_increases_cannot_overdraw_the_sender(make_world):
    """bob has 100 left after five 100-payments; five concurrent +100 corrections: one wins."""
    users = [m.user("bob", 600), m.user("cy", 0), m.user("ada", 0)]
    w = make_world(m.fixture(users))
    pays = [expect(w.bob.pay("cy", 100, note=str(i)), 201).json() for i in range(5)]
    assert w.bob.balance() == 100
    res = m.burst(lambda i: w.bob.correct(pays[i]["payment_id"], amount=200,
                                          effective_at=pays[i]["created_at"]), 5)
    m.assert_no_5xx(res)
    codes = m.codes(res)
    assert sum(1 for r in res if r.status_code == 201) == 1, codes
    assert all(r.status_code in (201, 409) for r in res)
    assert w.bob.balance() == 0 and w.cy.balance() == 600
    w.oracle()


def test_correction_races_a_payment_for_the_same_funds(make_world):
    users = [m.user("bob", 300), m.user("cy", 0), m.user("ada", 0)]
    for _ in range(5):
        w = make_world(m.fixture(users))
        p = expect(w.bob.pay("cy", 100), 201).json()                  # bob 200 left
        res = m.burst(lambda i: (w.bob.correct(p["payment_id"], amount=300,
                                               effective_at=p["created_at"])
                                 if i == 0 else w.bob.pay("ada", 150)), 2)
        m.assert_no_5xx(res)
        assert w.bob.balance() >= 0
        # one order or the other: both can never succeed (200 + 150 + ... > 300)
        assert [r.status_code for r in res].count(409) >= 1, m.codes(res)
        w.oracle()


def test_fifty_mixed_writers_keep_every_invariant(make_world):
    users = [m.user(h, 2_000) for h in ("ada", "bob", "cy", "dee")]
    w = make_world(m.fixture(users))
    handles = ["ada", "bob", "cy", "dee"]
    base = [expect(w.clients[h].pay(handles[(i + 1) % 4], 50 + i), 201).json()
            for i, h in enumerate(handles)]
    rng = random.Random(7)
    plan = [rng.choice(["pay", "pay", "correct", "auth", "stmt", "me"]) for _ in range(200)]

    def op(i):
        kind = plan[i]
        h = handles[i % 4]
        c = w.clients[h]
        if kind == "pay":
            return c.pay(handles[(i + 2) % 4], 1 + i % 40)
        if kind == "correct":
            mine = [p for p in base if p["from_handle"] == h]
            p = mine[0]
            cur = len(c.revisions(p["payment_id"])) if i % 3 else 1
            return c.correct(p["payment_id"], expected_revision=cur, amount=(i * 7) % 90,
                             effective_at=p["created_at"], reason=f"c{i}")
        if kind == "auth":
            return c.authorize(handles[(i + 1) % 4], 20 + i % 30)
        if kind == "stmt":
            return c.get("/statement", params={"limit": 5})
        return c.get("/me", params={"as_of": FAR})

    res = m.burst(op, 200)
    m.assert_no_5xx(res)
    for r in res:
        assert r.status_code in (200, 201, 409, 422), (r.status_code, r.text[:200])
    for r in res:
        if r.status_code == 409:
            assert r.json()["error"]["code"] in ("stale_revision", "insufficient_funds",
                                                 "historical_overdraft"), r.text
    assert sum(w.balances().values()) == w.total
    for t in (FAR, m.iso(m.now())):
        assert sum(c.me_at(as_of=t)["balance"] for c in w.clients.values()) == w.total
    w.oracle()


def test_snapshots_stay_stable_while_writers_run(make_world):
    pays = [m.seeded_payment(f"s_{i:03d}", "ada", "bob", 1 + i % 5, at(0, minutes=500 - 5 * i))
            for i in range(60)]
    w = make_world(m.history_fixture(pays, ending={"ada": 5_000, "bob": 5_000, "cy": 1_000}))
    first = w.ada.statement(limit=200)
    token, want = first["snapshot"], first["entries"]

    def op(i):
        if i % 5 == 0:
            p = w.ada.correct(f"s_{i % 60:03d}", expected_revision=1, amount=i % 4,
                              effective_at=at(0, minutes=500 - 5 * (i % 60)))
            return p
        if i % 5 in (1, 2):
            return w.ada.pay("bob", 1 + i % 3) if i % 2 else w.bob.pay("ada", 1)
        if i % 5 == 3:
            a = w.ada.authorize("cy", 10)
            return a
        return w.ada.get("/statement", params={"snapshot": token, "limit": 25, "offset": (i % 3) * 25})

    res = m.burst(op, 100)
    m.assert_no_5xx(res)
    for r in res:
        if r.request.url.params.get("snapshot"):
            assert r.status_code == 200
            off = int(r.request.url.params["offset"])
            assert r.json()["entries"] == want[off:off + 25]
            assert r.json()["closing_balance"] == first["closing_balance"]
    assert w.ada.statement(snapshot=token, limit=200)["entries"] == want
    w.oracle()


def test_concurrent_snapshot_creation_gives_distinct_consistent_tokens(make_world):
    w = make_world(m.fixture())
    for i in range(5):
        expect(w.ada.pay("bob", i + 1), 201)
    res = m.burst(lambda i: w.ada.get("/statement", params={"limit": 2}), 40)
    m.assert_no_5xx(res)
    tokens = {r.json()["snapshot"] for r in res}
    assert len(tokens) == 40
    assert len({tuple(e["payment"]["payment_id"] for e in r.json()["entries"]) for r in res}) == 1


@pytest.mark.parametrize("seed", [11, 12, 13])
def test_random_correction_sequences_match_a_reference_model(make_world, seed):
    """Corrections applied sequentially against a reference of the spec's balance rules."""
    rng = random.Random(seed)
    handles = ["ada", "bob", "cy"]
    users = [m.user(h, 1_000) for h in handles]
    w = make_world(m.fixture(users))
    bal = {h: 1_000 for h in handles}
    pays = []
    for i in range(6):
        a, b = rng.sample(handles, 2)
        amt = rng.choice([10, 50, 120])
        p = expect(w.clients[a].pay(b, amt), 201).json()
        bal[a] -= amt
        bal[b] += amt
        pays.append({"id": p["payment_id"], "from": a, "to": b, "amt": amt, "rev": 1,
                     "at": p["created_at"]})
    for step in range(25):
        p = rng.choice(pays)
        new = rng.choice([0, 5, 60, 200, 700])
        delta = new - p["amt"]
        payer, other = (p["from"], p["to"]) if delta >= 0 else (p["to"], p["from"])
        resp = w.clients[p["from"]].correct(p["id"], expected_revision=p["rev"], amount=new,
                                            effective_at=p["at"])
        if bal[payer] < abs(delta):
            assert resp.status_code == 409, (step, resp.text)
            continue
        if resp.status_code == 409:      # a historical overdraft the reference does not model
            assert resp.json()["error"]["code"] == "historical_overdraft"
            continue
        expect(resp, 201)
        bal[payer] -= abs(delta)
        bal[other] += abs(delta)
        p["amt"], p["rev"] = new, p["rev"] + 1
        assert w.balances() == bal, (step, w.balances(), bal)
    w.oracle()
