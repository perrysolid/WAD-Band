"""S2-R9 / D32 expiry by the clock (no background job), seeded expires_at in any offset,
calendar boundaries, and the D35 timestamp format (amends D10)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

import pf_model as m
from pf_client import expect, expect_error


def T(frm, to, amount, **extra):
    return {"from_handle": frm, "to_handle": to, "amount": amount, **extra}


def _sleep_past(expires_at: str, margin: float = 0.4, cap: float = 8.0) -> None:
    """Sleep until just after expires_at without touching the service."""
    wait = (m.parse(expires_at) - m.now()).total_seconds() + margin
    time.sleep(max(0.0, min(wait, cap)))


@pytest.fixture
def short(make_world):
    """ttl 2 s; Ada 10000, Bob 2500, Cy 500, Dee 0; Ada is an operator."""
    return make_world(m.fixture([m.ADA, m.BOB, m.CY, m.user("dee", 0)], ttl=2,
                                operators=["u_ada"]))


def test_a_hold_expires_on_its_own_and_reads_reflect_it(short):
    """[S2-R9] no request at the deadline; the first read afterwards shows the expiry."""
    a = expect(short.ada.authorize("bob", 3_000), 201).json()
    assert short.ada.me()["held"] == 3_000
    _sleep_past(a["expires_at"])
    ada = short.ada.me()
    assert (ada["total"], ada["available"], ada["held"]) == (10_000, 10_000, 0)
    got = short.bob.auth(a["authorization_id"])
    assert (got["status"], got["remaining_amount"], got["captured_amount"]) == ("expired", 0, 0)
    assert short.ada.auths(status="open") == []
    assert [x["authorization_id"] for x in short.ada.auths(status="expired")] == \
        [a["authorization_id"]]
    short.oracle()


def test_capture_and_void_after_expiry(short):
    a = expect(short.ada.authorize("bob", 3_000), 201).json()
    _sleep_past(a["expires_at"])
    expect_error(short.bob.capture(a["authorization_id"], {}), 409, "authorization_expired")
    expect_error(short.ada.void(a["authorization_id"]), 409, "authorization_not_open")
    assert short.bob.me()["total"] == 2_500
    short.oracle()


@pytest.mark.parametrize("write", ["pay", "authorize", "settle", "pay_request"])
def test_writes_see_the_expiry_without_a_prior_read(short, write):
    """[S2-R9] funds checks on every write path count an expired hold as released."""
    rid = expect(short.bob.ask("cy", 500), 201).json()["request_id"]
    a = expect(short.cy.authorize("ada", 500), 201).json()
    _sleep_past(a["expires_at"])
    if write == "pay":
        expect(short.cy.pay("bob", 500), 201)
    elif write == "authorize":
        expect(short.cy.authorize("bob", 500), 201)
    elif write == "settle":
        expect(short.ada.settle([T("cy", "dee", 500)]), 201)
    else:
        expect(short.cy.pay_request(rid, {}), 201)
    short.oracle()


def test_expiry_after_partial_captures_releases_only_the_remainder(short):
    """[S2-R5] expiry keeps all capture records."""
    a = expect(short.ada.authorize("bob", 2_000), 201).json()
    aid = a["authorization_id"]
    p = expect(short.bob.capture(aid, {"amount": 700, "final": False}), 201).json()
    _sleep_past(a["expires_at"])
    got = short.ada.auth(aid)
    assert (got["status"], got["captured_amount"], got["remaining_amount"]) == \
        ("expired", 700, 0)
    assert got["payment_ids"] == [p["payment_id"]] and got["payment_id"] == p["payment_id"]
    ada = short.ada.me()
    assert (ada["total"], ada["available"], ada["held"]) == (9_300, 9_300, 0)
    assert p["payment_id"] in {x["payment_id"] for x in short.cy.feed()}
    short.oracle()


def test_a_hold_is_open_before_its_deadline(make_world):
    w = make_world(m.fixture(ttl=30))
    a = expect(w.ada.authorize("bob", 100), 201).json()
    time.sleep(1.0)
    assert w.ada.auth(a["authorization_id"])["status"] == "open"
    expect(w.bob.capture(a["authorization_id"], {}), 201)
    w.oracle()


# ---- seeded expires_at: offsets, rollover, DST -------------------------------------------

def test_offsets_are_honoured_not_the_wall_clock(make_world):
    """[S2-R2] future written at -12:00 looks earlier; past written at +14:00 looks later."""
    fut = m.iso(m.now() + timedelta(hours=2), offset_minutes=-12 * 60)
    past = m.iso(m.now() - timedelta(hours=2), offset_minutes=14 * 60)
    w = make_world(m.fixture(authorizations=[
        m.hold("a_fut", "ada", "bob", 1_000, expires_at=fut),
        m.hold("a_past", "ada", "bob", 2_000, expires_at=past)]))
    got = {a["authorization_id"]: a for a in w.ada.auths()}
    assert got["a_fut"]["status"] == "open" and got["a_past"]["status"] == "expired"
    assert m.parse(got["a_fut"]["expires_at"]) == m.parse(fut)
    assert m.parse(got["a_past"]["expires_at"]) == m.parse(past)
    assert w.ada.me()["held"] == 1_000
    w.oracle()


@pytest.mark.parametrize("given,out", [
    ("2026-10-25T02:30:00+02:00", "2026-10-25T00:30:00+00:00"),   # CEST, before the DST end
    ("2026-10-25T02:30:00+01:00", "2026-10-25T01:30:00+00:00"),   # CET, the repeated hour
    ("2027-03-28T02:30:00+01:00", "2027-03-28T01:30:00+00:00"),   # the skipped local hour
    ("2027-01-01T01:30:00+05:30", "2026-12-31T20:00:00+00:00"),   # year rollover backwards
    ("2026-12-31T22:00:00-03:00", "2027-01-01T01:00:00+00:00"),   # year rollover forwards
    ("2028-02-29T12:00:00Z", "2028-02-29T12:00:00+00:00"),        # leap day, Z designator
    ("2099-12-31T23:59:59+00:00", "2099-12-31T23:59:59+00:00"),   # far future
    ("2100-01-01T05:00:00+05:00", "2100-01-01T00:00:00+00:00"),
    ("2026-12-01T10:00:00.250+00:00", "2026-12-01T10:00:00.250+00:00"),
    ("2026-12-01T10:00:00.5+00:00", "2026-12-01T10:00:00.500+00:00"),
    ("2026-12-01T10:00:00.000+00:00", "2026-12-01T10:00:00+00:00"),
    ("2026-11-30T23:30:00-01:00", "2026-12-01T00:30:00+00:00"),   # month rollover
], ids=["dst-cest", "dst-cet-repeat", "dst-gap", "year-back", "year-fwd", "leap-z",
        "far-future", "far-future-offset", "millis", "half-second", "zero-fraction",
        "month-rollover"])
def test_seeded_expires_at_is_reported_in_utc_per_d35(make_world, given, out):
    """[S2-R2, D35] any RFC 3339 offset in; the same instant out, UTC, `.mmm` only if non-zero."""
    w = make_world(m.fixture(authorizations=[m.hold("a_1", "ada", "bob", 100,
                                                    expires_at=given)]))
    a = w.ada.auth("a_1")
    assert a["expires_at"] == out
    is_open = m.parse(out) > m.now()
    assert a["status"] == ("open" if is_open else "expired")
    assert w.ada.me()["held"] == (100 if is_open else 0)


@pytest.mark.parametrize("given", ["1999-12-31T23:59:59-05:00", "1970-01-01T00:00:00Z",
                                   "2000-02-29T00:00:00+14:00"])
def test_long_past_open_holds_are_expired(make_world, given):
    w = make_world(m.fixture(authorizations=[m.hold("a_1", "ada", "bob", 50_000,
                                                    expires_at=given)]))
    assert w.ada.auth("a_1")["status"] == "expired"
    assert w.ada.me()["held"] == 0


@pytest.mark.parametrize("bad", ["2026-13-01T00:00:00+00:00", "2026-02-29T00:00:00+00:00",
                                 "2026-12-01T24:00:01+00:00", "2026-12-01T10:00:00+25:00",
                                 "2026-12-01 10:00:00+00:00x", "", 1_800_000_000, None])
def test_invalid_seeded_expires_at_is_422(make_world, reset, bad):
    w = make_world(m.fixture())
    resp = reset(m.fixture(authorizations=[m.hold("a_1", "ada", "bob", 1, expires_at=bad)]),
                 raw=True)
    expect_error(resp, 422, "validation_failed")
    assert w.ada.me()["total"] == 10_000


# ---- D35 timestamps -----------------------------------------------------------------------

def test_every_response_timestamp_follows_d35(op_world):
    w = op_world
    stamps = []
    for i in range(15):
        stamps.append(expect(w.ada.pay("bob", 1 + i), 201).json()["created_at"])
        time.sleep(0.013)
    stamps.append(expect(w.bob.ask("ada", 1), 201).json()["created_at"])
    s = expect(w.ada.split(10, ["ada", "bob"]), 201).json()
    stamps += [s["created_at"], s["requests"][0]["created_at"]]
    st = expect(w.ada.settle([T("bob", "cy", 1)]), 201).json()
    stamps += [st["committed_at"], st["payments"][0]["created_at"]]
    a = expect(w.ada.authorize("bob", 5), 201).json()
    stamps += [a["created_at"], a["expires_at"]]
    stamps.append(expect(w.bob.capture(a["authorization_id"]), 201).json()["created_at"])
    for ts in stamps:
        m.assert_d35(ts)
    assert any("." in ts for ts in stamps[:15]), \
        "15 payments 13 ms apart all landed on whole seconds; D35 fractions never exercised"


def test_lexical_order_equals_time_order(world):
    """[D35] 2026-..:00+00:00 sorts before 2026-..:00.250+00:00, as time does."""
    for i in range(12):
        expect(world.ada.pay("bob", 1 + i), 201)
        time.sleep(0.011)
    stamps = [p["created_at"] for p in world.ada.feed()]
    assert stamps == sorted(stamps, reverse=True)
    parsed = [m.parse(s) for s in stamps]
    assert parsed == sorted(parsed, reverse=True)
    pairs = sorted(zip(stamps, parsed))
    assert [p for _, p in pairs] == sorted(parsed)


def test_created_at_is_close_to_the_real_clock(world):
    p = expect(world.ada.pay("bob", 1), 201).json()
    skew = abs((m.parse(p["created_at"]) - datetime.now(timezone.utc)).total_seconds())
    assert skew < 30, f"created_at is {skew}s away from now"
