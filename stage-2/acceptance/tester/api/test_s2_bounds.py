"""DECISION D39 (no wallet above 2^53) and D40 (import bounds), carried into stage 2.

D39 covers every money-moving write; in stage 2 that adds captures. All sums are exact
Python ints; nothing here goes through a float.
"""
from __future__ import annotations

import copy
import json

import pytest

import pf_model as m
from pf_client import expect, expect_error, expect_one_of, new_key

TOP = 2 ** 53  # 9007199254740992


def _jpy(ada: int, *, bob: int = 1_000_000_000, operators=None, holds=None) -> dict:
    fx = m.fixture([m.user("ada", ada, display_name="Ada"), m.user("bob", bob),
                    m.user("cy", 0)], currency="JPY", operators=operators)
    if holds is not None:
        fx["authorizations"] = holds
    return fx


@pytest.mark.parametrize("amount", [1, 2, 1_000_000_000])
def test_payment_above_two_pow_53_is_422(make_world, amount):
    """[D39] bob pays ada (at 2^53) -> 422; nothing moves, no key claimed."""
    w = make_world(_jpy(TOP))
    before = w.wallets()
    key = new_key()
    expect_error(w.bob.pay("ada", amount, key=key), 422, "validation_failed")
    assert w.wallets() == before
    expect(w.ada.pay("cy", amount), 201)
    expect(w.bob.pay("ada", amount, key=key), 201)  # the key was never claimed
    assert w.ada.balance() == TOP
    w.oracle()


def test_insufficient_available_precedes_the_upper_bound(make_world):
    """[D39, S2-R10] a payer whose available is short gets 409 first, even when total isn't."""
    w = make_world(_jpy(TOP, bob=10))
    expect(w.bob.authorize("cy", 10), 201)  # bob: total 10, available 0
    expect_error(w.bob.pay("ada", 1), 409, "insufficient_funds")
    w.oracle()


def test_request_pay_above_two_pow_53_is_422(make_world):
    w = make_world(_jpy(TOP))
    r = expect(w.ada.ask("bob", 3), 201).json()
    expect_error(w.bob.pay_request(r["request_id"]), 422, "validation_failed")
    assert w.bob.requests_list()[0]["status"] == "pending"
    w.oracle()


def test_settlement_credit_above_two_pow_53_is_422(make_world):
    w = make_world(_jpy(TOP, operators=["u_cy"]))
    before = w.wallets()
    expect_error(w.cy.settle([{"from_handle": "bob", "to_handle": "ada", "amount": 2}]),
                 422, "validation_failed")
    assert w.wallets() == before and w.ada.feed() == []
    w.oracle()


@pytest.mark.parametrize("body", [{}, {"amount": 1}, {"amount": 1, "final": False}])
def test_capture_above_two_pow_53_is_422_and_the_hold_is_untouched(make_world, body):
    """[D39] a capture is a money-moving write: crediting ada above 2^53 -> 422 at the
    funds step. The hold stays open with its full remainder; the key is not claimed."""
    w = make_world(_jpy(TOP))
    a = expect(w.bob.authorize("ada", 5), 201).json()
    before = w.wallets()
    key = new_key()
    expect_error(w.ada.capture(a["authorization_id"], body, key=key), 422,
                 "validation_failed")
    assert w.wallets() == before
    st = w.ada.auth(a["authorization_id"])
    assert (st["status"], st["captured_amount"], st["remaining_amount"]) == ("open", 0, 5)
    assert st["payment_ids"] == [] and w.ada.feed() == []
    expect(w.ada.pay("cy", 5), 201)                  # room under the bound again
    p = expect(w.ada.capture(a["authorization_id"], body, key=key), 201).json()
    assert p["authorization_id"] == a["authorization_id"]
    assert p["amount"] == body.get("amount", 5)
    assert w.ada.balance() == TOP - 5 + p["amount"]   # ada paid 5 away, then captured
    w.oracle()


def test_capture_landing_exactly_on_two_pow_53_is_fine(make_world):
    w = make_world(_jpy(TOP - 5))
    a = expect(w.bob.authorize("ada", 9), 201).json()
    expect_error(w.ada.capture(a["authorization_id"], {"amount": 6}), 422, "validation_failed")
    expect(w.ada.capture(a["authorization_id"], {"amount": 5}), 201)  # final: releases 4
    assert w.ada.me()["total"] == TOP
    assert w.bob.me()["available"] == w.bob.me()["total"] == 1_000_000_000 - 5
    w.oracle()


def test_holds_do_not_count_toward_the_upper_bound(make_world):
    """[D39] a hold moves no money, so an incoming hold never trips the bound by itself."""
    w = make_world(_jpy(TOP))
    expect(w.bob.authorize("ada", 1_000_000_000), 201)
    assert w.ada.me()["total"] == TOP
    w.oracle()


# ---- D40 ------------------------------------------------------------------------------

def _export(control) -> dict:
    return expect(control.get("/_test/export"), 200).json()


def _import(control, body):
    return control.post("/_test/import", content=json.dumps(body),
                        headers={"Content-Type": "application/json"})


def _numeric_leaves(node, path=()):
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        yield path
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _numeric_leaves(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node[:3]):
            yield from _numeric_leaves(v, path + (i,))


def _get(doc, path):
    for p in path:
        doc = doc[p]
    return doc


def _set(doc, path, value):
    _get(doc, path[:-1])[path[-1]] = value


@pytest.fixture
def busy(make_world):
    """Every stage-2 record kind: payment, request, settlement, open/partial/closed holds."""
    w = make_world(m.fixture(operators=["u_ada"]))
    expect(w.ada.pay("bob", 100, note="x"), 201)
    expect(w.bob.ask("ada", 50), 201)
    expect(w.ada.settle([{"from_handle": "bob", "to_handle": "cy", "amount": 25}]), 201)
    a1 = expect(w.ada.authorize("bob", 700), 201).json()
    expect(w.bob.capture(a1["authorization_id"], {"amount": 200, "final": False}), 201)
    a2 = expect(w.ada.authorize("cy", 300), 201).json()
    expect(w.cy.capture(a2["authorization_id"]), 201)
    a3 = expect(w.bob.authorize("ada", 40), 201).json()
    expect(w.bob.void(a3["authorization_id"]), 200)
    return w


def test_clock_out_of_range_is_422_and_destination_unchanged(busy, control):
    """[D40] the architect's repro: state.clock = 9e15 -> 422; then a payment is 201."""
    snap = _export(control)
    if "clock" not in snap.get("state", {}):
        pytest.skip("this build's state has no top-level 'clock'; covered by the leaf fuzz")
    bad = copy.deepcopy(snap)
    bad["state"]["clock"] = 9e15
    expect_error(_import(control, bad), 422, "validation_failed")
    assert _export(control) == snap
    expect(busy.ada.pay("bob", 1), 201)
    busy.oracle()


@pytest.mark.parametrize("value", [9e15, TOP + 2, -1, 1.5, 253402300800000, 10 ** 30])
def test_out_of_range_numbers_anywhere_in_state_never_break_the_service(busy, control, value):
    """[D40 §5] each numeric field of a stage-2 state set out of range: import is 204 or
    422 (never 5xx); a 422 changes nothing; every read and write afterwards is < 500."""
    snap = _export(control)
    leaves = list(_numeric_leaves(snap["state"]))
    assert leaves, "export state has no numeric fields to probe"
    for path in leaves[:60]:
        bad = copy.deepcopy(snap)
        _set(bad["state"], path, value)
        resp = _import(control, bad)
        expect_one_of(resp, (204, None), (422, "validation_failed"))
        if resp.status_code == 422:
            assert _export(control) == snap, f"422 import of state{list(path)} changed state"
        for c in busy.clients.values():
            for p in ("/me", "/activity", "/requests", "/authorizations"):
                r = c.get(p)
                assert r.status_code < 500, f"after state{list(path)}={value}: {p} {r.status_code}"
        for r in (busy.ada.pay("bob", 1), busy.ada.authorize("bob", 1)):
            assert r.status_code < 500, f"write after state{list(path)}={value}: {r.text[:200]}"
        expect(_import(control, snap), 204)
    expect(busy.ada.pay("bob", 1), 201)
    busy.oracle()


def test_import_with_balance_or_hold_above_two_pow_53_is_rejected(make_world, control):
    """[D40] wallet values and hold amounts beyond their bounds -> 422, unchanged. Located by
    unusual sentinel values; TOP + 2 or more must be refused (TOP + 1 is not a distinct double)."""
    bal, held = 7_777_777_123, 333_333_331
    w = make_world(m.fixture([m.user("ada", bal), m.BOB, m.CY]))
    expect(w.ada.authorize("bob", held), 201)
    snap = _export(control)
    leaves = list(_numeric_leaves(snap["state"]))
    hits = [p for p in leaves if _get(snap["state"], p) in (bal, held)]
    if not hits:
        pytest.skip("balance/hold not stored as plain numbers in this state format")
    for path in hits:
        for value in (TOP + 2, 2 ** 60, 10 ** 20):  # TOP + 1 parses to 2^53 itself: untestable
            bad = copy.deepcopy(snap)
            _set(bad["state"], path, value)
            resp = _import(control, bad)
            assert resp.status_code == 422, \
                f"import with state{list(path)} = {value} -> {resp.status_code}; " \
                f"ada now reads {w.ada.me()}"
            assert _export(control) == snap
    me = w.ada.me()
    assert (me["total"], me["held"]) == (bal, held)
    w.oracle()


def test_import_with_balance_of_exactly_two_pow_53_is_accepted(make_world, control):
    """[D40] the bound is inclusive: 2^53 itself imports (204) and is served exactly. This is
    also what the literal 2^53+1 becomes after JSON parsing, so it cannot be refused."""
    sentinel = 7_777_777_123
    make_world(m.fixture([m.user("ada", sentinel), m.BOB, m.CY]))
    snap = _export(control)
    hits = [p for p in _numeric_leaves(snap["state"]) if _get(snap["state"], p) == sentinel]
    if not hits:
        pytest.skip("balance not stored as a plain number in this state format")
    ok = copy.deepcopy(snap)
    for path in hits[:1]:
        _set(ok["state"], path, TOP)
    expect(_import(control, ok), 204)
    expect(_import(control, snap), 204)  # restore
