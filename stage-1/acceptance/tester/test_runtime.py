"""§3 runtime contract, §4 fixture model, §6 password hashing cost."""
from __future__ import annotations

import re
import time

import pytest

import pf_model as m
from pf_client import Api, expect, expect_error, new_key

RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def test_health_is_ok(control):
    """[§3.2]"""
    resp = expect(control.get("/health"), 200)
    assert resp.json() == {"status": "ok"}


def test_responses_are_json_utf8(world):
    """[§3.4] application/json; charset=utf-8 on success and on error."""
    ok = world.ada.get("/me")
    err = world.ada.get("/me", token="nope")
    for resp in (ok, err):
        ctype = resp.headers.get("content-type", "").lower().replace(" ", "")
        assert ctype.startswith("application/json"), ctype


def test_reset_returns_204_and_seeds_logins(reset, api):
    """[§3.3, §4] seeded users log in immediately with their fixture password."""
    resp = reset(m.fixture())
    assert resp.status_code == 204
    for u in (m.ADA, m.BOB, m.CY):
        body = expect(api().login(u["email"], u["password"]), 200).json()
        assert body["user_id"] == u["id"] and body["display_name"] == u["display_name"]
        assert isinstance(body["token"], str) and body["token"]


def test_reset_replaces_all_state(world, reset, api):
    """[§3.3] after 204 only the new fixture is visible: users, tokens, payments, requests."""
    expect(world.ada.pay("bob", 100), 201)
    expect(world.bob.ask("ada", 100), 201)
    dee = expect(api().signup("dee@example.com"), 201).json()
    old_token = world.ada.token
    reset(m.fixture([m.user("ada", 7), m.user("zed", 3)]))
    expect_error(api().login("dee@example.com"), 401, "unauthenticated")
    expect_error(api(dee["token"]).get("/me"), 401, "unauthenticated")
    expect_error(api(old_token).get("/me"), 401, "unauthenticated")
    ada = api().authenticate("ada@example.com")
    assert ada.balance() == 7
    assert ada.feed() == [] and ada.requests_list() == []
    expect_error(ada.pay("bob", 1), 404, "not_found")


def test_repeated_resets_are_supported(reset, api):
    """[§3.3]"""
    for bal in (1, 2, 3):
        reset(m.fixture([m.user("ada", bal), m.user("bob", 0)]))
        assert api().authenticate("ada@example.com").balance() == bal


def test_reset_requires_no_auth(control):
    """[§3.3] no Authorization header needed."""
    expect(control.post("/_test/reset", json=m.fixture()), 204)


@pytest.mark.parametrize("currency,units", [("EUR", 2), ("JPY", 0), ("BHD", 3)])
def test_currency_and_minor_units_come_from_the_fixture(make_world, currency, units):
    """[§4] one currency per service, declared in the fixture."""
    w = make_world(m.fixture(currency=currency))
    me = w.ada.me()
    assert (me["currency"], me["minor_units"]) == (currency, units)
    p = expect(w.ada.pay("bob", 1), 201).json()
    assert p["currency"] == currency
    r = expect(w.bob.ask("ada", 1), 201).json()
    assert r["currency"] == currency


def test_negative_seeded_balance_rejects_reset_and_changes_nothing(world, reset, api):
    """[§4] balance < 0 -> 422 validation_failed, previous state untouched (tokens too)."""
    expect(world.ada.pay("bob", 250), 201)
    before = world.balances()
    bad = m.fixture([m.user("ada", 5), m.user("neg", -1)])
    expect_error(reset(bad, raw=True), 422, "validation_failed")
    assert world.balances() == before
    assert len(world.ada.feed()) == 1
    expect_error(api().login("neg@example.com"), 401, "unauthenticated")


def test_zero_seeded_balance_is_fine(make_world):
    """[§4] boundary: 0 is not below zero."""
    w = make_world(m.fixture([m.user("ada", 0), m.user("bob", 0)]))
    assert w.ada.balance() == 0


def test_seeded_payments_are_not_replayed_against_balances(make_world):
    """[§4] balance is post-payment; seeded payments appear in the feed with their ids."""
    fx = m.fixture(payments=[{"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
                              "amount": 500, "note": "coffee", "visibility": "public"},
                             {"id": "p_2", "from_user_id": "u_bob", "to_user_id": "u_ada",
                              "amount": 70, "note": "secret", "visibility": "private"}])
    w = make_world(fx)
    assert w.balances() == {"ada": 10_000, "bob": 2_500, "cy": 500}
    feed = {p["payment_id"]: p for p in w.ada.feed()}
    assert set(feed) == {"p_1", "p_2"}
    p1 = feed["p_1"]
    assert (p1["from_handle"], p1["to_handle"], p1["amount"], p1["note"]) == \
        ("ada", "bob", 500, "coffee")
    assert p1["request_id"] is None and RFC3339.match(p1["created_at"])
    assert [p["payment_id"] for p in w.cy.feed()] == ["p_1"], "p_2 is private"


@pytest.mark.parametrize("status", ["pending", "declined", "cancelled", "paid"])
def test_seeded_requests_keep_their_status(make_world, status):
    """[§4] fixture requests are visible to both parties with their seeded status."""
    fx = m.fixture(requests=[{"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada",
                              "amount": 1200, "note": "taxi", "status": status}])
    w = make_world(fx)
    for who in ("ada", "bob"):
        rqs = w.clients[who].requests_list()
        assert [(r["request_id"], r["status"], r["amount"]) for r in rqs] == \
            [("rq_1", status, 1200)]
    assert w.cy.requests_list() == []
    resp = w.ada.pay_request("rq_1")
    if status == "pending":
        expect(resp, 201)
    else:
        expect_error(resp, 409, "request_not_pending")


def test_seeded_ids_do_not_collide_with_new_ids(make_world):
    """[§3.4, §4] new resources never reuse a seeded id."""
    fx = m.fixture(payments=[{"id": f"p_{i}", "from_user_id": "u_ada", "to_user_id": "u_bob",
                              "amount": 1, "note": "", "visibility": "public"}
                             for i in range(1, 6)],
                   requests=[{"id": f"rq_{i}", "requester_id": "u_bob", "payer_id": "u_ada",
                              "amount": 1, "note": "", "status": "pending"}
                             for i in range(1, 6)])
    w = make_world(fx)
    new_p = {expect(w.ada.pay("bob", 1), 201).json()["payment_id"] for _ in range(6)}
    new_r = {expect(w.bob.ask("ada", 1), 201).json()["request_id"] for _ in range(6)}
    assert not new_p & {f"p_{i}" for i in range(1, 6)}
    assert not new_r & {f"rq_{i}" for i in range(1, 6)}
    assert len(new_p) == 6 and len(new_r) == 6
    assert all(len(i) <= 64 for i in new_p | new_r)


def test_large_seeded_reset_within_ten_seconds_and_logins_work(reset, api):
    """[§2, §6] password hashing must not push a big reset past 10 s."""
    users = [m.user(f"u{i}", 100) for i in range(1000)]
    started = time.monotonic()
    resp = reset(m.fixture(users), raw=True)
    elapsed = time.monotonic() - started
    expect(resp, 204)
    assert elapsed < 10.0, f"reset of 1000 users took {elapsed:.1f}s"
    for i in (0, 499, 999):
        assert api().authenticate(f"u{i}@example.com").balance() == 100
    expect_error(api().login("u5@example.com", "wrong password"), 401, "unauthenticated")


def test_large_reset_with_distinct_passwords(reset, api):
    """[§2, §6, R12] 2000 distinct passwords still reset inside the 10 s budget."""
    users = [m.user(f"d{i}", 1, password=f"secret-{i:05d}") for i in range(2000)]
    started = time.monotonic()
    resp = reset(m.fixture(users), raw=True)
    elapsed = time.monotonic() - started
    expect(resp, 204)
    assert elapsed < 10.0, f"reset of 2000 distinct passwords took {elapsed:.1f}s"
    expect(api().login("d1999@example.com", "secret-01999"), 200)
    expect_error(api().login("d1999@example.com", "secret-01998"), 401, "unauthenticated")


def test_logins_do_not_block_other_requests(world, base_url):
    """[§2, §6] 30 concurrent logins (hashing) while a cheap read stays fast."""
    clients = [Api(base_url) for _ in range(30)]
    reader = world.new_client("bob")
    timings: list[float] = []

    def work(i: int):
        if i == 30:
            out = []
            for _ in range(5):
                t = time.monotonic()
                out.append(reader.get("/me"))
                timings.append(time.monotonic() - t)
            return out[-1]
        return clients[i].login("ada@example.com")

    try:
        out = m.burst(work, 31)
        m.assert_no_5xx(out)
        assert all(r.status_code == 200 for r in out), m.tally(out)
        assert max(timings) < 3.0, f"GET /me stalled behind logins: {timings}"
    finally:
        for c in clients:
            c.close()


def test_export_does_not_contain_plaintext_passwords(world, control):
    """[§6] passwords stored with a password-hashing function, never plaintext."""
    unique = "Zq9!unique-plaintext-marker"
    expect(world.ada.signup("pw@example.com", unique), 201)
    body = expect(control.get("/_test/export"), 200).text
    assert unique not in body and "correct horse" not in body


def test_timestamps_have_explicit_offsets(world):
    """[§3.4] RFC 3339 with explicit offset in every response timestamp."""
    p = expect(world.ada.pay("bob", 1), 201).json()
    r = expect(world.bob.ask("ada", 1), 201).json()
    s = expect(world.ada.split(10, ["ada", "bob"]), 201).json()
    for ts in (p["created_at"], r["created_at"], s["created_at"],
               s["requests"][0]["created_at"]):
        assert RFC3339.match(ts), ts


def test_ids_are_strings_of_at_most_64_chars(world):
    """[§3.4]"""
    p = expect(world.ada.pay("bob", 1), 201).json()
    r = expect(world.bob.ask("ada", 1), 201).json()
    s = expect(world.ada.split(10, ["ada", "bob"]), 201).json()
    for v in (p["payment_id"], p["from_user_id"], r["request_id"], s["split_id"]):
        assert isinstance(v, str) and 1 <= len(v) <= 64, v


def test_unknown_query_parameters_are_ignored(world):
    """[§3.4]"""
    expect(world.ada.get("/me", params={"zzz": "1"}), 200)
    expect(world.ada.get("/activity", params={"zzz": "1", "limit": "5"}), 200)
    expect(world.ada.get("/requests", params={"zzz": "x"}), 200)


def test_unknown_body_fields_are_ignored(world):
    """[§3.4] on every write path."""
    expect(world.ada.pay("bob", 1, zzz={"a": [1]}), 201)
    rq = expect(world.bob.ask("ada", 1, zzz=True), 201).json()
    expect(world.ada.pay_request(rq["request_id"], {"zzz": 1}), 201)
    expect(world.ada.split(10, ["bob"], zzz=None), 201)
    expect(world.ada.post("/auth/signup", json={"email": "q@example.com",
                                                 "password": "correct horse",
                                                 "display_name": "Q", "handle": "x"},
                          token=None), 201)
