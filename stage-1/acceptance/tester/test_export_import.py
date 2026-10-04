"""§10 export and import; §11 state carried across reset/import."""
from __future__ import annotations

import copy
import json

import pytest

import pf_model as m
from pf_client import expect, expect_error, new_key


def T(frm, to, amount, **extra):
    return {"from_handle": frm, "to_handle": to, "amount": amount, **extra}


def _export(control) -> dict:
    body = expect(control.get("/_test/export"), 200).json()
    assert body["track"] == "pocketful" and body["format_version"] == 1
    assert isinstance(body["state"], dict)
    return body


def _import(control, snap) -> None:
    resp = control.post("/_test/import", content=json.dumps(snap),
                        headers={"Content-Type": "application/json"})
    expect(resp, 204)


@pytest.fixture
def rich(make_world, api):
    """A world with every kind of record: payments, requests in all states, a split,
    a settlement, a signed-up user, completed and failed idempotent requests."""
    w = make_world(m.fixture([m.ADA, m.BOB, m.CY, m.user("dee", 0)], operators=["u_ada"]))
    rec = {"keys": {}}
    k = new_key()
    rec["payment"] = expect(w.ada.pay("bob", 1500, note="dinner \U0001F37D", visibility="private",
                                      key=k), 201).json()
    rec["keys"]["payment"] = (k, {"to_handle": "bob", "amount": 1500,
                                  "note": "dinner \U0001F37D", "visibility": "private"})
    k = new_key()
    rec["request"] = expect(w.bob.ask("ada", 1200, note="taxi", key=k), 201).json()
    rec["keys"]["request"] = (k, {"payer_handle": "ada", "amount": 1200, "note": "taxi"})
    k = new_key()
    paid = expect(w.cy.ask("ada", 70), 201).json()
    rec["paid_payment"] = expect(w.ada.pay_request(paid["request_id"], {}, key=k), 201).json()
    rec["keys"]["pay"] = (k, paid["request_id"])
    declined = expect(w.bob.ask("cy", 5), 201).json()
    expect(w.cy.post(f"/requests/{declined['request_id']}/decline", json={}), 200)
    cancelled = expect(w.bob.ask("cy", 6), 201).json()
    expect(w.bob.post(f"/requests/{cancelled['request_id']}/cancel", json={}), 200)
    k = new_key()
    rec["split"] = expect(w.ada.split(1000, ["ada", "bob", "cy"], key=k), 201).json()
    rec["keys"]["split"] = (k, {"amount": 1000, "participant_handles": ["ada", "bob", "cy"]})
    k = new_key()
    rec["settlement"] = expect(w.ada.settle([T("bob", "dee", 100), T("dee", "cy", 40)], key=k),
                               201).json()
    rec["keys"]["settlement"] = (k, {"transfers": [T("bob", "dee", 100), T("dee", "cy", 40)]})
    k = new_key()
    expect_error(w.cy.pay("bob", 99_999, key=k), 409, "insufficient_funds")
    rec["keys"]["failed"] = k
    signup = expect(api().signup("eve@example.com", "eve password", "Eve"), 201).json()
    rec["eve_token"] = signup["token"]
    rec["balances"] = w.balances()
    rec["feeds"] = {h: c.feed() for h, c in w.clients.items()}
    rec["requests"] = {h: c.requests_list() for h, c in w.clients.items()}
    w.total = sum(rec["balances"].values())
    return w, rec


def test_export_shape_and_is_read_only(rich, control):
    """[§10] 200 {track, format_version: 1, state}; export changes nothing."""
    w, rec = rich
    a = _export(control)
    b = _export(control)
    assert a == b
    assert w.balances() == rec["balances"]


def test_round_trip_through_a_reset(rich, control, reset, api):
    """[§10] reset to a different fixture, import: everything is back, unregenerated."""
    w, rec = rich
    snap = _export(control)
    reset(m.fixture([m.user("zed", 5)]))
    _import(control, snap)
    # existing bearer tokens still work
    assert w.balances() == rec["balances"]
    assert api(rec["eve_token"]).me()["handle"] == "eve"
    # hashed-password login still works
    expect(api().login("eve@example.com", "eve password"), 200)
    expect_error(api().login("eve@example.com", "wrong password"), 401, "unauthenticated")
    expect(api().login("ada@example.com"), 200)
    # zed (destination data) is gone
    expect_error(api().login("zed@example.com"), 401, "unauthenticated")
    # payments, requests, timestamps and ids identical
    assert {h: c.feed() for h, c in w.clients.items()} == rec["feeds"]
    assert {h: c.requests_list() for h, c in w.clients.items()} == rec["requests"]
    w.oracle()


def test_receipts_replay_after_import(rich, control, reset):
    """[§10] completed idempotent bodies and original responses survive import."""
    w, rec = rich
    snap = _export(control)
    reset(m.fixture())
    _import(control, snap)
    k, body = rec["keys"]["payment"]
    assert expect(w.ada.post("/payments", json=body, key=k), 200).json() == rec["payment"]
    k, body = rec["keys"]["request"]
    assert expect(w.bob.post("/requests", json=body, key=k), 200).json() == rec["request"]
    k, rid = rec["keys"]["pay"]
    assert expect(w.ada.pay_request(rid, {}, key=k), 200).json() == rec["paid_payment"]
    k, body = rec["keys"]["split"]
    assert expect(w.ada.post("/splits", json=body, key=k), 200).json() == rec["split"]
    k, body = rec["keys"]["settlement"]
    assert expect(w.ada.post("/settlements", json=body, key=k), 200).json() == rec["settlement"]
    # different body under a claimed key is still a reuse
    k, body = rec["keys"]["payment"]
    expect_error(w.ada.pay("bob", 1, key=k), 409, "idempotency_key_reuse")
    assert w.balances() == rec["balances"]
    w.oracle()


def test_failed_keys_remain_reusable_after_import(rich, control, reset):
    w, rec = rich
    snap = _export(control)
    reset(m.fixture())
    _import(control, snap)
    expect(w.cy.pay("bob", 1, key=rec["keys"]["failed"]), 201)


def test_operators_and_settlement_membership_survive(rich, control, reset):
    """[§11] import preserves operator permissions and settlement membership."""
    w, rec = rich
    snap = _export(control)
    reset(m.fixture(operators=[]))
    _import(control, snap)
    expect(w.ada.settle([T("bob", "cy", 1)]), 201)
    expect_error(w.bob.settle([T("ada", "cy", 1)]), 403, "forbidden")
    sid = rec["settlement"]["settlement_id"]
    members = sorted((p for p in w.cy.feed() if p["settlement_id"] == sid),
                     key=lambda p: p["payment_id"])
    assert members == sorted(rec["settlement"]["payments"], key=lambda p: p["payment_id"])


def test_new_ids_do_not_collide_after_import(rich, control, reset, api):
    """[§10] id sequences carried: new records never reuse an imported id."""
    w, rec = rich
    snap = _export(control)
    old_payments = {p["payment_id"] for f in rec["feeds"].values() for p in f}
    old_requests = {r["request_id"] for rs in rec["requests"].values() for r in rs}
    reset(m.fixture())
    _import(control, snap)
    new_p = {expect(w.ada.pay("dee", 1), 201).json()["payment_id"] for _ in range(5)}
    new_r = {expect(w.dee.ask("ada", 1), 201).json()["request_id"] for _ in range(5)}
    s = expect(w.ada.split(10, ["ada", "dee"]), 201).json()
    st = expect(w.ada.settle([T("ada", "dee", 1)]), 201).json()
    assert not new_p & old_payments and not new_r & old_requests
    assert s["split_id"] != rec["split"]["split_id"]
    assert st["settlement_id"] != rec["settlement"]["settlement_id"]
    u = expect(api().signup("fay@example.com"), 201).json()
    assert u["user_id"] not in {"u_ada", "u_bob", "u_cy", "u_dee"}
    assert api(rec["eve_token"]).me()["user_id"] != u["user_id"]
    w.oracle()


def test_import_is_replacement_and_repeatable(rich, control, api):
    """[§10] repeating an import restores the state without duplicating anything."""
    w, rec = rich
    snap = _export(control)
    expect(w.ada.pay("bob", 1), 201)
    expect(api().signup("late@example.com"), 201)
    late_token = expect(api().login("late@example.com"), 200).json()["token"]
    for _ in range(2):
        _import(control, snap)
    assert {h: c.feed() for h, c in w.clients.items()} == rec["feeds"]
    assert {h: c.requests_list() for h, c in w.clients.items()} == rec["requests"]
    expect_error(api().login("late@example.com"), 401, "unauthenticated")
    expect_error(api(late_token).get("/me"), 401, "unauthenticated")
    w.oracle()


def test_export_is_a_snapshot_not_a_live_view(rich, control):
    w, rec = rich
    snap = _export(control)
    frozen = copy.deepcopy(snap)
    expect(w.ada.pay("bob", 3), 201)
    assert _export(control) != frozen
    _import(control, frozen)
    assert w.balances() == rec["balances"]


def test_pending_request_still_payable_after_import(rich, control, reset):
    w, rec = rich
    snap = _export(control)
    reset(m.fixture())
    _import(control, snap)
    rid = rec["request"]["request_id"]
    p = expect(w.ada.pay_request(rid), 201).json()
    assert p["request_id"] == rid
    split_rq = rec["split"]["requests"][0]
    expect(w.bob.pay_request(split_rq["request_id"]), 201)
    w.oracle()


def test_reset_clears_imported_state(rich, control, reset, api):
    w, rec = rich
    snap = _export(control)
    _import(control, snap)
    reset(m.fixture())
    expect_error(api(rec["eve_token"]).get("/me"), 401, "unauthenticated")
    ada = api().authenticate("ada@example.com")
    assert ada.feed() == [] and ada.balance() == 10_000
    k, body = rec["keys"]["payment"]
    expect(ada.post("/payments", json=body, key=k), 201)


@pytest.mark.parametrize("mutate", [
    lambda s: s.pop("track"), lambda s: s.pop("format_version"), lambda s: s.pop("state"),
    lambda s: s.__setitem__("track", "tablekeeper"),
    lambda s: s.__setitem__("format_version", 999),
    lambda s: s.__setitem__("format_version", "1"),
    lambda s: s.__setitem__("state", "garbage"),
    lambda s: s.__setitem__("state", {}),
    lambda s: s.__setitem__("state", None),
    lambda s: s.__setitem__("state", {"bogus": [1, 2, 3]}),
], ids=["no-track", "no-version", "no-state", "wrong-track", "wrong-version", "string-version",
        "string-state", "empty-state", "null-state", "bogus-state"])
def test_invalid_import_is_422_and_changes_nothing(rich, control, api, mutate):
    """[§10] missing fields, wrong track/version or an invalid state -> 422, no change."""
    w, rec = rich
    snap = _export(control)
    bad = copy.deepcopy(snap)
    mutate(bad)
    expect(w.ada.pay("bob", 9), 201)  # destination differs from the snapshot
    before = w.balances()
    resp = control.post("/_test/import", content=json.dumps(bad),
                        headers={"Content-Type": "application/json"})
    expect_error(resp, 422, "validation_failed")
    assert w.balances() == before
    expect(api(rec["eve_token"]).get("/me"), 200)


@pytest.mark.parametrize("raw", ["{", "not json", ""])
def test_import_unparseable_is_400(rich, control, raw):
    w, rec = rich
    resp = control.post("/_test/import", content=raw, headers={"Content-Type": "application/json"})
    expect_error(resp, 400, "malformed_request")
    assert w.balances() == rec["balances"]


@pytest.mark.parametrize("raw", ["[]", "5", "null"])
def test_import_non_object(rich, control, raw):
    w, rec = rich
    """[D18 clarification] a parsed non-object import body -> 422, state unchanged."""
    resp = control.post("/_test/import", content=raw, headers={"Content-Type": "application/json"})
    expect_error(resp, 422, "validation_failed")
    assert w.balances() == rec["balances"]


def test_export_under_concurrent_writes_is_consistent(make_world, control, reset):
    """[§10] export is an atomic snapshot: imported totals still conserve."""
    users = [m.user(f"w{i}", 1000) for i in range(10)]
    w = make_world(m.fixture(users))
    clients = [w.new_client(f"w{i % 10}") for i in range(40)]
    snaps: list = []

    def go(i):
        if i >= 40:
            snaps.append(control.get("/_test/export"))
            return snaps[-1]
        return clients[i].pay(f"w{(i + 1) % 10}", 37)

    out = m.burst(go, 45)
    m.assert_no_5xx(out)
    for snap in snaps:
        _import(control, expect(snap, 200).json())
        w.oracle()


def test_snapshot_from_a_different_currency_restores_currency(make_world, control, reset, api):
    w = make_world(m.fixture(currency="JPY"))
    expect(w.ada.pay("bob", 5), 201)
    snap = _export(control)
    reset(m.fixture(currency="BHD"))
    _import(control, snap)
    me = w.ada.me()
    assert (me["currency"], me["minor_units"], me["balance"]) == ("JPY", 0, 9_995)


def test_import_of_large_state_within_ten_seconds(make_world, control, reset):
    """[§10] test control calls have a 10 s budget, with many accounts and records."""
    users = [m.user(f"x{i}", 1000) for i in range(300)]
    w = make_world(m.fixture(users))
    for i in range(50):
        expect(w.clients[f"x{i}"].pay(f"x{i + 1}", 1), 201)
    snap = _export(control)
    reset(m.fixture())
    _import(control, snap)  # control client has a 10 s timeout
    assert w.clients["x0"].balance() == 999
