"""S2-R11 / S2 'Concurrent operations': 50 writers contending for holds, captures, voids,
payments and settlements. Winners are counted exactly and the oracle runs after each."""
from __future__ import annotations

import random
import time

import pf_model as m
from pf_client import expect, expect_error


def T(frm, to, amount, **extra):
    return {"from_handle": frm, "to_handle": to, "amount": amount, **extra}


def _clients(w, handle, n):
    return [w.new_client(handle) for _ in range(n)]


def test_fifty_holds_drain_available_exactly(make_world):
    """1000 available, 50 x 30 holds: exactly 33 succeed, 10 left, total untouched."""
    w = make_world(m.fixture([m.user("ada", 1_000), m.user("bob", 0)]))
    adas = _clients(w, "ada", 50)
    out = m.burst(lambda i: adas[i].authorize("bob", 30), 50)
    m.assert_no_5xx(out)
    assert m.tally(out) == {201: 33, 409: 17}, m.tally(out)
    for r in out:
        if r.status_code == 409:
            expect_error(r, 409, "insufficient_funds")
    assert w.oracle()["ada"] == (1_000, 10, 990)
    assert len(w.ada.auths(status="open")) == 33


def test_holds_and_payments_contend_for_the_same_available(make_world):
    w = make_world(m.fixture([m.user("ada", 1_000), m.user("bob", 0)]))
    adas = _clients(w, "ada", 50)
    out = m.burst(lambda i: adas[i].pay("bob", 40) if i % 2 else adas[i].authorize("bob", 40),
                  50)
    m.assert_no_5xx(out)
    assert m.tally(out) == {201: 25, 409: 25}, m.tally(out)
    pays = sum(1 for i, r in enumerate(out) if i % 2 and r.status_code == 201)
    holds = 25 - pays
    assert w.oracle()["ada"] == (1_000 - 40 * pays, 0, 40 * holds)


def test_partial_captures_never_exceed_the_authorized_amount(make_world):
    """Σ captures <= amount: 50 x 30 non-final captures on a 1000 hold -> exactly 33."""
    w = make_world(m.fixture([m.user("ada", 1_000), m.user("bob", 0)]))
    aid = expect(w.ada.authorize("bob", 1_000), 201).json()["authorization_id"]
    bobs = _clients(w, "bob", 50)
    out = m.burst(lambda i: bobs[i].capture(aid, {"amount": 30, "final": False}), 50)
    m.assert_no_5xx(out)
    assert m.tally(out) == {201: 33, 422: 17}, m.tally(out)
    for r in out:
        if r.status_code == 422:
            expect_error(r, 422, "capture_exceeds_authorization")
    a = w.ada.auth(aid)
    assert (a["status"], a["captured_amount"], a["remaining_amount"]) == ("open", 990, 10)
    assert len(a["payment_ids"]) == 33 and len(set(a["payment_ids"])) == 33
    assert w.oracle() == {"ada": (10, 0, 10), "bob": (990, 990, 0)}


def test_racing_final_captures_close_the_hold_once(make_world):
    w = make_world(m.fixture([m.user("ada", 1_000), m.user("bob", 0)]))
    aid = expect(w.ada.authorize("bob", 600), 201).json()["authorization_id"]
    bobs = _clients(w, "bob", 50)
    out = m.burst(lambda i: bobs[i].capture(aid, {"amount": 1 + i}), 50)
    m.assert_no_5xx(out)
    assert m.tally(out) == {201: 1, 409: 49}, m.tally(out)
    won = next(r.json() for r in out if r.status_code == 201)
    for r in out:
        if r.status_code == 409:
            expect_error(r, 409, "authorization_not_open")
    assert w.oracle() == {"ada": (1_000 - won["amount"],) * 2 + (0,),
                          "bob": (won["amount"],) * 2 + (0,)}


def test_capture_and_void_race_serializes(make_world):
    """Either one capture wins and every void is refused, or a void wins and no capture."""
    for _ in range(3):
        w = make_world(m.fixture([m.user("ada", 1_000), m.user("bob", 0)]))
        aid = expect(w.ada.authorize("bob", 800), 201).json()["authorization_id"]
        bobs, adas = _clients(w, "bob", 25), _clients(w, "ada", 25)
        out = m.burst(lambda i: bobs[i // 2].capture(aid, {}) if i % 2 == 0
                      else adas[i // 2].void(aid), 50)
        m.assert_no_5xx(out)
        caps = [r for i, r in enumerate(out) if i % 2 == 0]
        voids = [r for i, r in enumerate(out) if i % 2 == 1]
        cap_wins = [r for r in caps if r.status_code == 201]
        assert len(cap_wins) <= 1, m.tally(caps)
        for r in caps:
            if r.status_code != 201:
                expect_error(r, 409, "authorization_not_open")
        final = w.ada.auth(aid)
        if cap_wins:
            assert final["status"] == "captured"
            for r in voids:
                expect_error(r, 409, "authorization_not_open")
            assert w.oracle() == {"ada": (200, 200, 0), "bob": (800, 800, 0)}
        else:
            assert final["status"] == "voided"
            assert all(r.status_code == 200 for r in voids), m.tally(voids)
            assert all(r.json() == voids[0].json() for r in voids)
            assert w.oracle() == {"ada": (1_000, 1_000, 0), "bob": (0, 0, 0)}


def test_payer_spending_cannot_eat_a_hold_being_captured(make_world):
    """45 x 10 payments race 10 x 60 captures of a 600 hold on a 1000 wallet."""
    w = make_world(m.fixture([m.user("ada", 1_000), m.user("bob", 0), m.user("cy", 0)]))
    aid = expect(w.ada.authorize("bob", 600), 201).json()["authorization_id"]
    adas, bobs = _clients(w, "ada", 45), _clients(w, "bob", 5)
    out = m.burst(lambda i: adas[i].pay("cy", 10) if i < 45
                  else bobs[i - 45].capture(aid, {"amount": 120, "final": False}), 50)
    m.assert_no_5xx(out)
    pays, caps = out[:45], out[45:]
    assert m.tally(pays) == {201: 40, 409: 5}, m.tally(pays)
    assert m.tally(caps) == {201: 5}, m.tally(caps)
    assert w.oracle() == {"ada": (0, 0, 0), "bob": (600, 600, 0), "cy": (400, 400, 0)}
    assert w.ada.auth(aid)["status"] == "captured"


def test_concurrent_voids_of_one_hold_agree(world):
    aid = expect(world.ada.authorize("bob", 500), 201).json()["authorization_id"]
    adas = _clients(world, "ada", 50)
    out = m.burst(lambda i: adas[i].void(aid), 50)
    m.assert_no_5xx(out)
    assert m.tally(out) == {200: 50}
    assert all(r.json() == out[0].json() for r in out)
    assert world.oracle()["ada"] == (10_000, 10_000, 0)


def test_settlements_and_holds_contend_for_one_wallet(op_world):
    """bob 2500: 25 settlements debiting 100 race 25 holds of 100 -> exactly 25 winners."""
    w = op_world
    expect(w.ada.pay("bob", 50), 201)                       # bob 2550: 25 fit, 50 left over
    adas, bobs = _clients(w, "ada", 25), _clients(w, "bob", 25)
    out = m.burst(lambda i: adas[i // 2].settle([T("bob", "dee", 100)]) if i % 2 == 0
                  else bobs[i // 2].authorize("cy", 100), 50)
    m.assert_no_5xx(out)
    assert m.tally(out) == {201: 25, 409: 25}, m.tally(out)
    for r in out:
        if r.status_code == 409:
            expect_error(r, 409, "insufficient_funds")
    settled = sum(1 for i, r in enumerate(out) if i % 2 == 0 and r.status_code == 201)
    held = 25 - settled
    assert w.oracle()["bob"] == (2_550 - 100 * settled, 50, 100 * held)


def test_a_hold_expiring_during_a_capture_storm(make_world):
    """Captures racing the deadline: each either moves money or is 409 expired, never both."""
    w = make_world(m.fixture([m.user("ada", 1_000), m.user("bob", 0)], ttl=2))
    a = expect(w.ada.authorize("bob", 1_000), 201).json()
    bobs, adas = _clients(w, "bob", 25), _clients(w, "ada", 25)
    lead = (m.parse(a["expires_at"]) - m.now()).total_seconds() - 0.05
    time.sleep(max(0.0, lead))
    out = m.burst(lambda i: bobs[i // 2].capture(a["authorization_id"],
                                                 {"amount": 20, "final": False})
                  if i % 2 == 0 else adas[i // 2].pay("bob", 30), 50)
    m.assert_no_5xx(out)
    caps = [r for i, r in enumerate(out) if i % 2 == 0]
    pays = [r for i, r in enumerate(out) if i % 2 == 1]
    for r in caps:
        if r.status_code != 201:
            expect_error(r, 409, "authorization_expired")
    for r in pays:
        if r.status_code != 201:
            expect_error(r, 409, "insufficient_funds")
    time.sleep(0.5)
    cap_wins = sum(r.status_code == 201 for r in caps)
    pay_wins = sum(r.status_code == 201 for r in pays)
    final = w.ada.auth(a["authorization_id"])
    assert final["status"] == "expired" and final["captured_amount"] == 20 * cap_wins
    assert w.oracle() == {"ada": (1_000 - 20 * cap_wins - 30 * pay_wins,) * 2 + (0,),
                          "bob": (20 * cap_wins + 30 * pay_wins,) * 2 + (0,)}


def test_random_mixed_storm_keeps_every_invariant(make_world):
    """50 workers, random authorize/capture/void/pay/request-pay across five wallets."""
    users = [m.user(h, b) for h, b in (("ada", 3_000), ("bob", 2_000), ("cy", 1_000),
                                         ("dee", 500), ("eve", 0))]
    w = make_world(m.fixture(users, operators=["u_ada"]))
    handles = [u["handle"] for u in users]
    pool = {h: _clients(w, h, 10) for h in handles}
    seed_auths = []
    for frm, to in (("ada", "bob"), ("bob", "cy"), ("cy", "dee"), ("ada", "eve")):
        seed_auths.append(expect(w.clients[frm].authorize(to, 300), 201).json())
    rng = random.Random(20261005)
    plan = []
    for i in range(150):
        kind = rng.choice(["pay", "auth", "cap", "cap", "void", "settle"])
        plan.append((kind, rng.randrange(10), rng.choice(handles), rng.choice(handles),
                     rng.choice([1, 7, 50, 120, 301]), rng.choice(seed_auths), rng.random()))

    def step(i):
        kind, slot, a, b, amt, auth, r = plan[i]
        if kind == "pay":
            return pool[a][slot].pay(b, amt)
        if kind == "auth":
            return pool[a][slot].authorize(b, amt)
        if kind == "cap":
            body = {"amount": amt, "final": r < 0.3} if r < 0.8 else {}
            return pool[auth["to_handle"]][slot].capture(auth["authorization_id"], body)
        if kind == "void":
            return pool[auth["from_handle"]][slot].void(auth["authorization_id"])
        return pool["ada"][slot].settle([T(a, b, amt)])

    out = m.burst(step, 150)
    m.assert_no_5xx(out)
    allowed = {201, 200, 403, 404, 409, 422}
    assert all(r.status_code in allowed for r in out), m.tally(out)
    w.oracle()
