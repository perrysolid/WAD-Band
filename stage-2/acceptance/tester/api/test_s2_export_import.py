"""S2-R13 / D34: a stage-2 export round-trips authorizations (every field, status and
capture list), the ttl, hold state, receipts and counters. (Stage-1 -> stage-2 is in
the upgrade suite, which needs the stage-1 service.)"""
from __future__ import annotations

import copy
import json
import time
from datetime import timedelta

import pytest

import pf_model as m
from conftest import World
from pf_client import expect, expect_error, new_key


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
def rich(make_world):
    """Holds in every status, partial captures, receipts for both new paths, a failed key."""
    past = m.iso(m.now() - timedelta(hours=2))
    w = make_world(m.fixture([m.ADA, m.BOB, m.CY, m.user("dee", 0)], ttl=900,
                             operators=["u_ada"],
                             authorizations=[m.hold("a_seed", "bob", "cy", 400, note="seed"),
                                             m.hold("a_seed_past", "cy", "ada", 100,
                                                    expires_at=past)]))
    rec: dict = {"keys": {}}
    k = new_key()
    body = {"to_handle": "bob", "amount": 2_000, "note": "deposit \U0001F3E0",
            "visibility": "private"}
    rec["open"] = expect(w.ada.post("/authorizations", json=body, key=k), 201).json()
    rec["keys"]["authorize"] = (k, body)
    aid = rec["open"]["authorization_id"]
    k = new_key()
    rec["cap1"] = expect(w.bob.capture(aid, {"amount": 300, "final": False}, key=k), 201).json()
    rec["keys"]["capture"] = (k, aid, {"amount": 300, "final": False})
    expect(w.bob.capture(aid, {"amount": 200, "final": False}), 201)
    done = expect(w.ada.authorize("cy", 500), 201).json()["authorization_id"]
    expect(w.cy.capture(done, {"amount": 100}), 201)
    voided = expect(w.cy.authorize("dee", 50), 201).json()["authorization_id"]
    expect(w.cy.void(voided), 200)
    k = new_key()
    expect_error(w.bob.capture(aid, {"amount": 99_999}, key=k), 422,
                 "capture_exceeds_authorization")
    rec["keys"]["failed"] = (k, aid)
    expect(w.ada.pay("dee", 10), 201)
    rec["rid"] = expect(w.dee.ask("ada", 70), 201).json()["request_id"]
    rec["state"] = _state(w)
    return w, rec


def _state(w: World) -> dict:
    return {"wallets": w.oracle(),
            "auths": {h: c.auths() for h, c in w.clients.items()},
            "feeds": {h: c.feed() for h, c in w.clients.items()},
            "requests": {h: c.requests_list() for h, c in w.clients.items()}}


def test_round_trip_through_a_reset(rich, control, reset, api):
    w, rec = rich
    snap = _export(control)
    reset(m.fixture([m.user("zed", 5)]))
    _import(control, snap)
    w2 = World(w.fx, api)
    w2.total = w.total
    assert _state(w2) == rec["state"]
    # existing tokens still work
    assert w.ada.me()["held"] == rec["state"]["wallets"]["ada"][2]


def test_import_is_replacement_and_repeatable(rich, control, api):
    w, rec = rich
    snap = _export(control)
    expect(w.ada.authorize("bob", 1), 201)
    expect(w.bob.capture(rec["open"]["authorization_id"], {"amount": 1, "final": False}), 201)
    _import(control, snap)
    _import(control, snap)
    assert _state(w) == rec["state"]


def test_receipts_on_new_paths_replay_after_import(rich, control):
    w, rec = rich
    snap = _export(control)
    _import(control, snap)
    k, body = rec["keys"]["authorize"]
    assert expect(w.ada.post("/authorizations", json=body, key=k), 200).json() == rec["open"]
    expect_error(w.ada.post("/authorizations", json={**body, "amount": 1}, key=k), 409,
                 "idempotency_key_reuse")
    k, aid, cbody = rec["keys"]["capture"]
    assert expect(w.bob.capture(aid, cbody, key=k), 200).json() == rec["cap1"]
    expect_error(w.bob.capture(aid, {}, key=k), 409, "idempotency_key_reuse")
    k, aid = rec["keys"]["failed"]
    expect(w.bob.capture(aid, {"amount": 1, "final": False}, key=k), 201)
    w.oracle()


def test_imported_holds_keep_working(rich, control):
    """Holds still reserve funds, can be captured and voided; ttl and ids carry on."""
    w, rec = rich
    _import(control, _export(control))
    aid = rec["open"]["authorization_id"]
    ada = w.ada.me()
    expect_error(w.ada.pay("cy", ada["available"] + 1), 409, "insufficient_funds")
    p = expect(w.bob.capture(aid, {"amount": 1_000, "final": False}), 201).json()
    assert w.bob.auth(aid)["payment_ids"][-1] == p["payment_id"]
    assert len(w.bob.auth(aid)["payment_ids"]) == 3
    expect(w.cy.capture("a_seed", {"amount": 400}), 201)
    new = expect(w.ada.authorize("bob", 5), 201).json()
    assert m.parse(new["expires_at"]) - m.parse(new["created_at"]) == timedelta(seconds=900)
    before_ids = {a["authorization_id"] for a in rec["state"]["auths"]["ada"]}
    assert new["authorization_id"] not in before_ids
    ids = [a["authorization_id"] for a in w.ada.auths()]
    assert len(ids) == len(set(ids))
    payment_ids = [x["payment_id"] for x in w.ada.feed()]
    assert len(payment_ids) == len(set(payment_ids))
    expect(w.ada.void(aid), 200)
    w.oracle()


def test_expiry_keeps_running_across_export_and_import(make_world, control):
    """A hold exported before its deadline is expired when read after it."""
    w = make_world(m.fixture(ttl=2))
    a = expect(w.ada.authorize("bob", 4_000), 201).json()
    snap = _export(control)
    time.sleep(max(0.0, (m.parse(a["expires_at"]) - m.now()).total_seconds() + 0.4))
    _import(control, snap)
    got = w.ada.auth(a["authorization_id"])
    assert got["status"] == "expired" and w.ada.me()["held"] == 0
    expect_error(w.bob.capture(a["authorization_id"]), 409, "authorization_expired")
    w.oracle()


def test_export_under_concurrent_hold_traffic_is_consistent(make_world, control, api):
    w = make_world(m.fixture([m.user("ada", 5_000), m.user("bob", 0)]))
    aid = expect(w.ada.authorize("bob", 2_000), 201).json()["authorization_id"]
    adas, bobs = [w.new_client("ada") for _ in range(20)], [w.new_client("bob") for _ in range(20)]
    snaps = []

    def go(i):
        if i % 5 == 0:
            snaps.append(control.get("/_test/export"))
            return snaps[-1]
        if i % 2:
            return adas[i % 20].authorize("bob", 37)
        return bobs[i % 20].capture(aid, {"amount": 13, "final": False})

    out = m.burst(go, 50)
    m.assert_no_5xx(out)
    for s in snaps:
        _import(control, expect(s, 200).json())
        w.oracle()


def test_invalid_import_changes_nothing(rich, control):
    w, rec = rich
    snap = _export(control)
    bad = copy.deepcopy(snap)
    if "schema_version" in bad["state"]:
        bad["state"]["schema_version"] = 99
    else:
        bad["state"] = {"bogus": True}
    expect(w.ada.pay("bob", 9), 201)
    before = _state(w)
    resp = control.post("/_test/import", content=json.dumps(bad),
                        headers={"Content-Type": "application/json"})
    expect_error(resp, 422, "validation_failed")
    assert _state(w) == before


def test_export_contains_no_plaintext_password(rich, control):
    assert "correct horse" not in control.get("/_test/export").text
