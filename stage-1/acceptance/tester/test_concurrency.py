"""§1 invariants under up to 50 concurrent writers (§2 budget). Oracle after each."""
from __future__ import annotations

import random
import threading
import time

import pf_model as m
from pf_client import expect


def _clients(w, handle, n):
    return [w.new_client(handle) for _ in range(n)]


def test_fifty_payers_drain_one_wallet_in_parts(make_world):
    """[§1.1, §1.2] 1000 drained by 50 x 30: exactly 33 succeed, 10 left."""
    w = make_world(m.fixture([m.user("ada", 1000), m.user("bob", 0)]))
    adas = _clients(w, "ada", 50)
    out = m.burst(lambda i: adas[i].pay("bob", 30), 50)
    m.assert_no_5xx(out)
    assert m.tally(out) == {201: 33, 409: 17}, m.tally(out)
    assert w.balances() == {"ada": 10, "bob": 990}
    w.oracle()


def test_fifty_all_or_nothing_attempts_on_one_wallet(make_world):
    w = make_world(m.fixture([m.user("ada", 1000), m.user("bob", 0)]))
    adas = _clients(w, "ada", 50)
    out = m.burst(lambda i: adas[i].pay("bob", 1000), 50)
    m.assert_no_5xx(out)
    assert m.tally(out) == {201: 1, 409: 49}
    w.oracle()


def test_opposite_directions_between_the_same_wallets(make_world):
    """[§1.1] 25 ada->bob and 25 bob->ada at once: no deadlock, exact accounting."""
    for _ in range(3):
        w = make_world(m.fixture([m.user("ada", 1000), m.user("bob", 1000)]))
        adas, bobs = _clients(w, "ada", 25), _clients(w, "bob", 25)
        out = m.burst(lambda i: adas[i // 2].pay("bob", 100) if i % 2 == 0
                      else bobs[i // 2].pay("ada", 100), 50)
        m.assert_no_5xx(out)
        a_wins = sum(1 for i, r in enumerate(out) if i % 2 == 0 and r.status_code == 201)
        b_wins = sum(1 for i, r in enumerate(out) if i % 2 == 1 and r.status_code == 201)
        assert all(r.status_code in (201, 409) for r in out), m.tally(out)
        assert w.ada.balance() == 1000 - 100 * a_wins + 100 * b_wins
        w.oracle()


def test_ring_of_three_wallets(make_world):
    """[§1.1] a -> b -> c -> a concurrently; lock ordering must not deadlock."""
    w = make_world(m.fixture([m.user("a", 300), m.user("b", 300), m.user("c", 300)]))
    nxt = {"a": "b", "b": "c", "c": "a"}
    cl = {h: _clients(w, h, 17) for h in "abc"}
    out = m.burst(lambda i: cl["abc"[i % 3]][i // 3].pay(nxt["abc"[i % 3]], 50), 50)
    m.assert_no_5xx(out)
    w.oracle()


def test_many_wallets_random_transfers_conserve(make_world):
    """[§1.1, §1.2] 50 wallets, 200 random transfers in waves of 50."""
    rng = random.Random(1234)
    users = [m.user(f"w{i:02d}", rng.randint(0, 500)) for i in range(50)]
    w = make_world(m.fixture(users))
    handles = [u["handle"] for u in users]
    plan = [(rng.choice(handles), rng.choice(handles), rng.randint(1, 300),
             rng.choice(["public", "private"])) for _ in range(200)]
    plan = [p for p in plan if p[0] != p[1]]
    pool = {h: w.new_client(h) for h in handles}  # httpx clients are thread-safe

    def go(i):
        a, b, x, vis = plan[i]
        return pool[a].pay(b, x, visibility=vis)

    out = m.burst(go, len(plan))
    m.assert_no_5xx(out)
    assert all(getattr(r, "status_code", 0) in (201, 409) for r in out), m.tally(out)
    w.oracle()


def test_hot_receiver(make_world):
    """[§1.1] 50 senders pay one receiver at once; every unit arrives."""
    users = [m.user("hot", 0)] + [m.user(f"s{i}", 10) for i in range(50)]
    w = make_world(m.fixture(users))
    out = m.burst(lambda i: w.clients[f"s{i}"].pay("hot", 10), 50)
    m.assert_no_5xx(out)
    assert m.tally(out) == {201: 50}
    assert w.hot.balance() == 500
    w.oracle()


def test_balance_never_observed_negative(make_world):
    """[§1.2] including transiently: poll the draining wallet throughout the burst."""
    w = make_world(m.fixture([m.user("ada", 500), m.user("bob", 0)]))
    adas = _clients(w, "ada", 45)
    watcher = w.new_client("ada")
    seen: list[int] = []
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            r = watcher.get("/me")
            if r.status_code == 200:
                seen.append(r.json()["balance"])

    t = threading.Thread(target=watch)
    t.start()
    try:
        out = m.burst(lambda i: adas[i].pay("bob", 20 + (i % 7)), 45)
    finally:
        stop.set()
        t.join()
    m.assert_no_5xx(out)
    assert seen and min(seen) >= 0, min(seen)
    w.oracle()


def test_requests_and_direct_payments_drain_together(make_world):
    """[§1.2, §1.3] request pays and direct payments contend for one wallet."""
    w = make_world(m.fixture([m.user("ada", 1000), m.user("bob", 0), m.user("cy", 0)]))
    rids = [expect(w.bob.ask("ada", 100), 201).json()["request_id"] for _ in range(20)]
    adas = _clients(w, "ada", 50)

    def go(i):
        if i < 20:
            return adas[i].pay_request(rids[i])
        if i < 40:
            return adas[i].pay("cy", 100)
        return adas[i].pay_request(rids[i - 40])  # second key on the same request

    out = m.burst(go, 50)
    m.assert_no_5xx(out)
    assert sum(1 for r in out if r.status_code == 201) == 10, m.tally(out)
    assert w.ada.balance() == 0
    paid = [r for r in w.bob.requests_list() if r["status"] == "paid"]
    assert len(paid) == sum(1 for r in out[:20] + out[40:] if r.status_code == 201)
    assert len({r["payment_id"] for r in paid}) == len(paid)
    w.oracle()


def test_splits_and_request_creation_under_load(world):
    """[§8, §9] 50 concurrent split/request creations: each counted once, no 5xx."""
    adas = _clients(world, "ada", 25)
    bobs = _clients(world, "bob", 25)
    out = m.burst(lambda i: adas[i // 2].split(10, ["ada", "bob", "cy"]) if i % 2 == 0
                  else bobs[i // 2].ask("ada", 1), 50)
    m.assert_no_5xx(out)
    assert m.tally(out) == {201: 50}
    assert len(world.cy.requests_list()) == 25
    assert len(world.ada.requests_list()) == 25 * 2 + 25
    world.oracle()


def test_no_request_exceeds_the_timeout_under_load(make_world):
    """[§2] 50 in flight, each answered well inside 5 s."""
    users = [m.user(f"t{i}", 100) for i in range(50)]
    w = make_world(m.fixture(users))
    timings: list[float] = [0.0] * 50

    def go(i):
        t = time.monotonic()
        r = w.clients[f"t{i}"].pay(f"t{(i + 1) % 50}", 1) if i % 2 else \
            w.clients[f"t{i}"].get("/activity")
        timings[i] = time.monotonic() - t
        return r

    out = m.burst(go, 50)
    m.assert_no_5xx(out)
    assert max(timings) < 5.0, max(timings)
