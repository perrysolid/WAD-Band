"""Overshoot guard: stage 2 must expose nothing from stages 3 and 4."""
from __future__ import annotations

import os
from datetime import timedelta

import pytest

import pf_model as m
from pf_client import expect, expect_error, new_key

# DECISION PF_STAGE: a stage-3+ build legitimately has this surface; its own suite guards the next.
pytestmark = pytest.mark.skipif(int(os.environ.get("PF_STAGE", "2")) >= 3,
                                reason="stage-2 overshoot guard; PF_STAGE>=3")

LATER = [("GET", "/statement"), ("GET", "/payments/{pid}/revisions"),
         ("POST", "/payments/{pid}/corrections"), ("POST", "/payments/{pid}/refunds"),
         ("POST", "/correction-batches"), ("GET", "/history"), ("GET", "/payments/{pid}")]


@pytest.mark.parametrize("method,path", LATER, ids=[f"{a} {b}" for a, b in LATER])
def test_later_stage_endpoints_do_not_exist(op_world, method, path):
    pid = expect(op_world.ada.pay("bob", 5), 201).json()["payment_id"]
    kw = {"json": {"amount": 1, "effective_at": "2026-01-01T00:00:00+00:00"}} \
        if method == "POST" else {}
    resp = op_world.ada.request(method, path.format(pid=pid), key=new_key(), **kw)
    expect_error(resp, 404, "not_found")
    assert op_world.ada.balance() == 9_995


def test_no_history_screen(control):
    resp = control.get("/history", headers={"Accept": "text/html"})
    assert resp.status_code == 404 and "data-testid" not in resp.text


def test_payments_and_authorizations_have_no_later_stage_fields(world):
    later = {"refund_of", "refunds", "effective_at", "recorded_at", "known_at", "revision",
             "revisions", "corrected", "closed_at", "as_of"}
    p = expect(world.ada.pay("bob", 1), 201).json()
    a = expect(world.ada.authorize("bob", 5), 201).json()
    c = expect(world.bob.capture(a["authorization_id"]), 201).json()
    for body in (p, a, c, world.ada.auth(a["authorization_id"]), world.ada.me()):
        assert not later & set(body), (later & set(body), body)


def test_me_ignores_as_of_and_known_at(world):
    expect(world.ada.pay("bob", 1), 201)
    for params in ({"as_of": "2000-01-01T00:00:00+00:00"}, {"known_at": "garbage"},
                   {"as_of": "not a time", "known_at": "2000-01-01T00:00:00Z"}):
        me = expect(world.ada.get("/me", params=params), 200).json()
        assert me["balance"] == 9_999 and me["available"] == 9_999


def test_seeded_payment_created_at_is_not_a_stage3_input(reset, api):
    """Stage 3 rejects a future seeded created_at; stage 2 ignores the field (D10)."""
    future = m.iso(m.now() + timedelta(days=400))
    fx = m.fixture(payments=[{"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
                              "amount": 5, "note": "", "visibility": "public",
                              "created_at": future}])
    expect(reset(fx, raw=True), 204)
    ada = api().authenticate("ada@example.com")
    (p,) = ada.feed()
    assert m.parse(p["created_at"]) < m.now() + timedelta(minutes=5)


def test_correction_of_a_capture_is_not_a_thing(world):
    a = expect(world.ada.authorize("bob", 5), 201).json()
    c = expect(world.bob.capture(a["authorization_id"]), 201).json()
    expect_error(world.ada.post(f"/payments/{c['payment_id']}/corrections",
                                json={"amount": 1}, key=new_key()), 404, "not_found")
