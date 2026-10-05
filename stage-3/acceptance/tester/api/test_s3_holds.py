"""Historical holds (stage-3 'Historical holds') and closed_at."""
from __future__ import annotations

from datetime import timedelta

import pytest

import pf_model as m
from pf_client import expect
from hist import at

FAR = "2999-01-01T00:00:00+00:00"


def money4(me):
    return me["balance"], me["total"], me["held"], me["available"]


def check(me, total, held):
    assert me["balance"] == me["total"] == total, me
    assert me["held"] == held and me["available"] == total - held, me


@pytest.fixture
def seeded_hold_world(make_world):
    fx = m.history_fixture([m.seeded_payment("p_1", "ada", "bob", 500, at(5))],
                           authorizations=[m.hold("a1", "ada", "bob", 2_000, created_at=at(2),
                                                  expires_at=m.iso(m.now() + timedelta(hours=2)))])
    return make_world(fx)


def test_seeded_hold_exists_from_its_created_at(seeded_hold_world):
    w = seeded_hold_world
    check(w.ada.me_at(as_of=at(3)), 10_000, 0)                 # before it existed
    check(w.ada.me_at(as_of=at(2)), 10_000, 2_000)             # inclusive start
    check(w.ada.me_at(as_of=at(1)), 10_000, 2_000)
    check(w.ada.me(), 10_000, 2_000)
    assert w.bob.me_at(as_of=at(1))["held"] == 0                 # receivers hold nothing


def test_an_open_hold_expires_at_its_deadline_for_future_queries(seeded_hold_world):
    w = seeded_hold_world
    soon = m.iso(m.now() + timedelta(hours=1, minutes=30))
    check(w.ada.me_at(as_of=soon), 10_000, 2_000)
    after = m.iso(m.now() + timedelta(hours=2, seconds=5))
    check(w.ada.me_at(as_of=after), 10_000, 0)
    check(w.ada.me_at(as_of=FAR), 10_000, 0)
    check(w.ada.me_at(as_of=FAR, known_at=FAR), 10_000, 0)


def test_known_at_before_the_hold_was_known_hides_it(make_world):
    w = make_world(m.fixture())
    a = expect(w.ada.authorize("bob", 700), 201).json()
    c = a["created_at"]
    before = m.iso(m.parse(c) - timedelta(seconds=2))
    check(w.ada.me_at(known_at=before), 10_000, 0)
    check(w.ada.me_at(known_at=c), 10_000, 700)
    within = m.iso(m.parse(c) + timedelta(minutes=5))              # inside the 600 s lifetime
    check(w.ada.me_at(known_at=c, as_of=within), 10_000, 700)      # deadline is known once created
    after = m.iso(m.parse(c) + timedelta(minutes=11))
    check(w.ada.me_at(known_at=c, as_of=after), 10_000, 0)         # ...so it has expired by then
    check(w.ada.me_at(as_of=before), 10_000, 0)
    check(w.ada.me_at(as_of=c), 10_000, 700)


def test_lifecycle_events_change_the_history_at_their_event_times(world):
    w = world
    a = expect(w.ada.authorize("bob", 1_000), 201).json()
    aid, t0 = a["authorization_id"], a["created_at"]
    assert w.ada.auth(aid)["closed_at"] is None
    c1 = expect(w.bob.capture(aid, {"amount": 300, "final": False}), 201).json()
    t1 = c1["created_at"]
    c2 = expect(w.bob.capture(aid, {"amount": 200, "final": True}), 201).json()
    t2 = c2["created_at"]
    ada = w.ada
    pre = m.iso(m.parse(t0) - timedelta(seconds=1))
    check(ada.me_at(as_of=pre), 10_000, 0)
    if m.parse(t0) < m.parse(t1):
        check(ada.me_at(as_of=t0), 10_000, 1_000)
    if m.parse(t1) < m.parse(t2):
        check(ada.me_at(as_of=t1), 9_700, 700)
    check(ada.me_at(as_of=t2), 9_500, 0)                      # final capture released the rest
    check(ada.me(), 9_500, 0)
    closed = ada.auth(aid)
    assert closed["status"] == "captured" and closed["remaining_amount"] == 0
    assert m.parse(closed["closed_at"]) == m.parse(t2)
    m.assert_d35(closed["closed_at"])
    assert w.bob.auth(aid)["closed_at"] == closed["closed_at"]
    check(w.bob.me_at(as_of=t2), 2_500 + 500, 0)
    w.oracle()


def test_void_releases_at_the_void_time(world):
    w = world
    a = expect(w.ada.authorize("bob", 800), 201).json()
    aid, t0 = a["authorization_id"], a["created_at"]
    v = expect(w.ada.void(aid), 200).json()
    assert m.parse(v["closed_at"]) >= m.parse(t0) and v["status"] == "voided"
    if m.parse(v["closed_at"]) > m.parse(t0):
        check(w.ada.me_at(as_of=t0), 10_000, 800)
    check(w.ada.me_at(as_of=v["closed_at"]), 10_000, 0)
    check(w.ada.me_at(as_of=FAR), 10_000, 0)
    assert w.ada.void(aid).json()["closed_at"] == v["closed_at"], "voiding again keeps closed_at"


def test_closed_at_is_null_while_open_and_present_in_every_view(world):
    a = expect(world.ada.authorize("bob", 5), 201).json()
    assert a["closed_at"] is None
    assert world.ada.auth(a["authorization_id"])["closed_at"] is None
    assert world.bob.auth(a["authorization_id"])["closed_at"] is None
    expect(world.ada.void(a["authorization_id"]), 200)
    assert world.bob.auth(a["authorization_id"])["closed_at"] is not None


def test_a_hold_that_expired_by_the_clock_closes_at_its_deadline(make_world):
    exp = at(3)
    fx = m.history_fixture([], authorizations=[
        m.hold("a_old", "ada", "bob", 400, created_at=at(5), expires_at=exp)])
    w = make_world(fx)
    a = w.ada.auth("a_old")
    assert a["status"] == "expired" and a["remaining_amount"] == 0
    assert m.parse(a["closed_at"]) == m.parse(exp)
    check(w.ada.me(), 10_000, 0)
    check(w.ada.me_at(as_of=at(4)), 10_000, 400)               # alive between creation and deadline
    check(w.ada.me_at(as_of=at(2)), 10_000, 0)
    check(w.ada.me_at(as_of=at(5)), 10_000, 400)


def test_expiry_takes_effect_exactly_at_expires_at(make_world):
    exp = at(0, hours=3)
    fx = m.history_fixture([], authorizations=[
        m.hold("a_x", "ada", "bob", 400, created_at=at(1), expires_at=m.iso(m.now() + timedelta(hours=1)))])
    w = make_world(fx)
    deadline = w.ada.auth("a_x")["expires_at"]
    check(w.ada.me_at(as_of=m.iso(m.parse(deadline) - timedelta(seconds=1))), 10_000, 400)
    check(w.ada.me_at(as_of=deadline), 10_000, 0)
    assert exp


def test_partial_capture_then_expiry_releases_only_the_remainder(make_world):
    fx = m.history_fixture([], authorizations=[
        m.hold("a_p", "ada", "bob", 1_000, created_at=at(1), expires_at=m.iso(m.now() + timedelta(hours=2)))])
    w = make_world(fx)
    c = expect(w.bob.capture("a_p", {"amount": 400, "final": False}), 201).json()
    check(w.ada.me(), 9_600, 600)
    deadline = w.ada.auth("a_p")["expires_at"]
    after = m.iso(m.parse(deadline) + timedelta(seconds=1))
    check(w.ada.me_at(as_of=after), 9_600, 0)
    check(w.ada.me_at(as_of=c["created_at"]), 9_600, 600)
    check(w.ada.me_at(as_of=at(1)), 10_000, 1_000)
    check(w.ada.me_at(as_of=after, known_at=at(0, minutes=-10)), 9_600, 0)
    w.oracle()


def test_statement_has_money_movements_only_and_captures_once(world):
    a = expect(world.ada.authorize("bob", 900), 201).json()
    cap = expect(world.bob.capture(a["authorization_id"], {"amount": 250}), 201).json()
    expect(world.ada.authorize("cy", 10), 201)
    st = world.ada.statement()
    assert [e["payment"]["payment_id"] for e in st["entries"]] == [cap["payment_id"]]
    e = st["entries"][0]
    assert e["delta"] == -250 and e["payment"]["authorization_id"] == a["authorization_id"]
    assert e["payment"]["request_id"] is None
    assert [x["payment"]["payment_id"] for x in world.bob.statement()["entries"]] == [cap["payment_id"]]
    assert world.ada.statement()["closing_balance"] == 9_750 == world.ada.me()["total"]
    world.oracle()


def test_available_view_never_negative_in_any_historical_instant(world):
    for i in range(5):
        a = expect(world.ada.authorize("bob", 1_000), 201).json()
        if i % 2:
            expect(world.bob.capture(a["authorization_id"], {"amount": 400, "final": False}), 201)
    for t in (at(0, minutes=5), m.iso(m.now()), FAR):
        for c in world.clients.values():
            me = c.me_at(as_of=t)
            assert me["available"] >= 0 and me["available"] == me["total"] - me["held"], me
        assert sum(c.me_at(as_of=t)["total"] for c in world.clients.values()) == world.total
    world.oracle()
