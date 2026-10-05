"""DECISION D39 (balance upper bound 2^53) and D40 (import bounds).

All sums are exact Python ints; nothing here goes through a float.
"""
from __future__ import annotations

import copy
import json

import pytest

import pf_model as m
from pf_client import expect, expect_error, expect_one_of, new_key

TOP = 2 ** 53  # 9007199254740992


def _jpy(ada: int, *, bob: int = 1000, operators: list[str] | None = None) -> dict:
    return m.fixture([m.user("ada", ada, display_name="Ada"), m.user("bob", bob),
                      m.user("cy", 0)], currency="JPY", operators=operators)


# ---- D39 ---------------------------------------------------------------------

@pytest.mark.parametrize("amount", [1, 2, 1_000_000_000])
def test_payment_above_two_pow_53_is_422_and_changes_nothing(make_world, amount):
    """[D39 §4] bob pays ada (at 2^53) -> 422, balances and feeds unchanged."""
    w = make_world(_jpy(TOP, bob=1_000_000_000))
    before = w.balances()
    key = new_key()
    expect_error(w.bob.pay("ada", amount, key=key), 422, "validation_failed")
    assert w.balances() == before and sum(before.values()) == TOP + 1_000_000_000
    assert w.bob.feed() == [] and w.ada.feed() == []
    w.oracle()


def test_payment_reaching_exactly_two_pow_53_is_allowed(make_world):
    """[D39] the bound is 'above 2^53': landing on 2^53 itself is fine; one more is not."""
    w = make_world(_jpy(TOP - 1))
    expect(w.bob.pay("ada", 1), 201)
    assert w.ada.balance() == TOP
    expect_error(w.bob.pay("ada", 1), 422, "validation_failed")
    assert w.ada.balance() == TOP
    w.oracle()


def test_insufficient_funds_precedes_the_upper_bound(make_world):
    """[D39] checked after insufficient_funds: a short payer gets 409, not 422."""
    w = make_world(_jpy(TOP))
    expect_error(w.cy.pay("ada", 1), 409, "insufficient_funds")
    expect_error(w.bob.pay("ada", 1001), 409, "insufficient_funds")
    w.oracle()


def test_upper_bound_refusal_claims_no_key(make_world):
    """[D39 §7] the 422 claims no key: the same key later succeeds as a first use (201)."""
    w = make_world(_jpy(TOP))
    key = new_key()
    expect_error(w.bob.pay("ada", 5, key=key), 422, "validation_failed")
    expect(w.ada.pay("cy", 5), 201)  # ada back to 2^53 - 5
    first = expect(w.bob.pay("ada", 5, key=key), 201).json()
    assert expect(w.bob.pay("ada", 5, key=key), 200).json() == first
    assert w.ada.balance() == TOP
    w.oracle()


def test_request_pay_above_two_pow_53_is_422_and_request_stays_pending(make_world):
    """[D39] paying a request that would push the requester above 2^53 -> 422, pending."""
    w = make_world(_jpy(TOP))
    r = expect(w.ada.ask("bob", 7), 201).json()
    key = new_key()
    before = w.balances()
    expect_error(w.bob.pay_request(r["request_id"], key=key), 422, "validation_failed")
    assert w.balances() == before
    st = [x for x in w.bob.requests_list() if x["request_id"] == r["request_id"]][0]
    assert st["status"] == "pending" and st["payment_id"] is None
    expect(w.ada.pay("cy", 7), 201)
    p = expect(w.bob.pay_request(r["request_id"], key=key), 201).json()  # key not claimed
    assert p["request_id"] == r["request_id"] and w.ada.balance() == TOP
    w.oracle()


@pytest.mark.parametrize("amount", [1, 2, 1_000_000_000])
def test_settlement_credit_above_two_pow_53_is_422(make_world, amount):
    """[D39 §11] a settlement crediting ada above 2^53 -> 422, nothing commits."""
    w = make_world(_jpy(TOP, bob=1_000_000_000, operators=["u_cy"]))
    before = w.balances()
    feeds = {h: c.feed() for h, c in w.clients.items()}
    key = new_key()
    expect_error(w.cy.settle([{"from_handle": "bob", "to_handle": "ada", "amount": amount}],
                             key=key), 422, "validation_failed")
    assert w.balances() == before
    assert {h: c.feed() for h, c in w.clients.items()} == feeds
    w.oracle()


def test_settlement_bound_is_on_the_net_result(make_world):
    """[D39 §11] the bound applies to the balance the settlement leaves: a batch that
    credits and debits ada by the same amount leaves her at 2^53 and commits."""
    w = make_world(_jpy(TOP, operators=["u_cy"]))
    s = expect(w.cy.settle([{"from_handle": "bob", "to_handle": "ada", "amount": 10},
                            {"from_handle": "ada", "to_handle": "cy", "amount": 10}]), 201).json()
    assert len(s["payments"]) == 2
    assert w.ada.balance() == TOP and w.bob.balance() == 990 and w.cy.balance() == 10
    w.oracle()


def test_settlement_insufficient_funds_precedes_the_upper_bound(make_world):
    """[D39] one entry would overdraw bob, another would push ada past 2^53 -> 409."""
    w = make_world(_jpy(TOP, operators=["u_cy"]))
    expect_error(w.cy.settle([{"from_handle": "bob", "to_handle": "ada", "amount": 2000}]),
                 409, "insufficient_funds")
    w.oracle()


# ---- D40 ---------------------------------------------------------------------

def _export(control) -> dict:
    return expect(control.get("/_test/export"), 200).json()


def _import(control, body) -> object:
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


def _set(doc, path, value):
    for p in path[:-1]:
        doc = doc[p]
    doc[path[-1]] = value


@pytest.fixture
def busy(make_world):
    w = make_world(m.fixture(operators=["u_ada"]))
    expect(w.ada.pay("bob", 100, note="x"), 201)
    r = expect(w.bob.ask("ada", 50), 201).json()
    expect(w.ada.settle([{"from_handle": "bob", "to_handle": "cy", "amount": 25}]), 201)
    return w, r


def _assert_still_serving(w):
    """After a refused import: writes and reads behave normally, never 5xx."""
    p = w.ada.pay("bob", 1)
    assert p.status_code == 201, p.text
    for c in w.clients.values():
        for path in ("/me", "/activity", "/requests"):
            assert c.get(path).status_code == 200
    w.oracle()


def test_import_with_clock_out_of_range_is_422_and_destination_unchanged(busy, control):
    """[D40] the architect's repro: state.clock = 9e15 -> 422; then a payment is 201."""
    w, _ = busy
    snap = _export(control)
    if "clock" not in snap.get("state", {}):
        pytest.skip("this build's state has no top-level 'clock'; covered by the leaf fuzz")
    bad = copy.deepcopy(snap)
    bad["state"]["clock"] = 9e15
    expect_error(_import(control, bad), 422, "validation_failed")
    assert _export(control) == snap
    _assert_still_serving(w)


@pytest.mark.parametrize("value", [9e15, TOP + 1, -1, 1.5, 253402300800000, 10 ** 30])
def test_out_of_range_numbers_anywhere_in_state_never_break_the_service(busy, control, value):
    """[D40 §5] every numeric field of the state, set out of range: import answers 204 or
    422 (never 5xx); a 422 leaves the destination unchanged; either way the service keeps
    serving writes without a 5xx."""
    w, _ = busy
    snap = _export(control)
    leaves = list(_numeric_leaves(snap["state"]))
    assert leaves, "export state has no numeric fields to probe"
    for path in leaves[:40]:
        bad = copy.deepcopy(snap)
        _set(bad["state"], path, value)
        resp = _import(control, bad)
        expect_one_of(resp, (204, None), (422, "validation_failed"))
        if resp.status_code == 422:
            assert _export(control) == snap, f"422 import of state{list(path)} changed state"
        for c in w.clients.values():
            for p in ("/me", "/activity", "/requests"):
                r = c.get(p)
                assert r.status_code < 500, f"after state{list(path)}={value}: {r.status_code}"
        r = w.ada.pay("bob", 1)
        assert r.status_code < 500, f"pay after state{list(path)}={value}: {r.text[:200]}"
        expect(_import(control, snap), 204)  # restore and continue
    _assert_still_serving(w)


def test_import_with_balance_above_two_pow_53_is_rejected(make_world, control):
    """[D40] a wallet value above 2^53 in imported state -> 422, unchanged. Located by
    searching the state for ada's exact seeded balance (an unusual sentinel)."""
    sentinel = 7_777_777_123
    w = make_world(m.fixture([m.user("ada", sentinel), m.BOB, m.CY]))
    snap = _export(control)
    hits = [p for p in _numeric_leaves(snap["state"]) if _get(snap["state"], p) == sentinel]
    if not hits:
        pytest.skip("balance not stored as a plain number in this state format")
    for path in hits:
        for value in (TOP + 1, TOP + 2, 10 ** 20):  # TOP + 1 must not be rounded to 2^53
            bad = copy.deepcopy(snap)
            _set(bad["state"], path, value)
            resp = _import(control, bad)
            assert resp.status_code == 422, \
                f"import with state{list(path)} = {value} -> {resp.status_code}; " \
                f"ada's balance now reads {w.ada.balance()}"
            expect_error(resp, 422, "validation_failed")
            assert _export(control) == snap
    assert w.ada.balance() == sentinel
    w.oracle()


def _get(doc, path):
    for p in path:
        doc = doc[p]
    return doc
