"""Stable statement pagination (stage-3 'Stable statement pagination')."""
from __future__ import annotations

import pytest

import pf_model as m
from pf_client import expect, expect_error
from hist import at


@pytest.fixture
def sw(make_world):
    """ada <-> bob with 30 seeded payments so a few pages exist."""
    pays = [m.seeded_payment(f"s_{i:03d}", "ada" if i % 3 else "bob", "bob" if i % 3 else "ada",
                             1 + i, at(0, minutes=600 - 10 * i)) for i in range(30)]
    return make_world(m.history_fixture(pays, ending={"ada": 5_000, "bob": 5_000, "cy": 100}))


def test_every_first_response_carries_an_opaque_snapshot(sw):
    a = sw.ada.statement(limit=5)
    b = sw.ada.statement(limit=5)
    assert isinstance(a["snapshot"], str) and a["snapshot"]
    assert a["snapshot"] != b["snapshot"], "each read freezes its own snapshot"
    assert sw.cy.statement()["snapshot"] != a["snapshot"]


def test_snapshot_paging_reproduces_the_exact_result(sw):
    first = sw.ada.statement(limit=7)
    token = first["snapshot"]
    full = sw.ada.statement(limit=200)
    seen = list(first["entries"])
    offset = 7
    while True:
        page = sw.ada.statement(snapshot=token, limit=7, offset=offset)
        assert page["opening_balance"] == full["opening_balance"] == first["opening_balance"]
        assert page["closing_balance"] == full["closing_balance"] == first["closing_balance"]
        assert page["snapshot"] == token or "snapshot" in page
        seen.extend(page["entries"])
        if not page["has_more"]:
            break
        offset += 7
    assert seen == full["entries"] and len(seen) == 30
    assert first["has_more"] is True


def test_snapshot_survives_new_payments_corrections_and_lifecycle(sw):
    first = sw.ada.statement(limit=10)
    token, want = first["snapshot"], sw.ada.statement(limit=200)["entries"]
    closing = first["closing_balance"]
    for i in range(4):
        expect(sw.ada.pay("bob", 3 + i), 201)
        expect(sw.bob.pay("ada", 2), 201)
    expect(sw.ada.correct("s_001", expected_revision=1, amount=0, effective_at=at(0, minutes=590)), 201)
    a = expect(sw.ada.authorize("bob", 40), 201).json()
    expect(sw.bob.capture(a["authorization_id"], {"amount": 10, "final": False}), 201)
    expect(sw.ada.void(a["authorization_id"]), 200)
    got = []
    for off in (0, 10, 20, 30, 40):
        page = sw.ada.statement(snapshot=token, limit=10, offset=off)
        assert page["closing_balance"] == closing
        got.extend(page["entries"])
    assert got == want
    fresh = sw.ada.statement(limit=200)
    assert fresh["closing_balance"] != closing and len(fresh["entries"]) > 30
    sw.oracle()


def test_snapshot_end_of_range_and_has_more(sw):
    token = sw.ada.statement(limit=1)["snapshot"]
    last = sw.ada.statement(snapshot=token, limit=7, offset=28)
    assert len(last["entries"]) == 2 and last["has_more"] is False        # final partial page
    exact = sw.ada.statement(snapshot=token, limit=10, offset=20)
    assert len(exact["entries"]) == 10 and exact["has_more"] is False
    beyond = sw.ada.statement(snapshot=token, limit=10, offset=500)
    assert beyond["entries"] == [] and beyond["has_more"] is False
    assert beyond["closing_balance"] == last["closing_balance"]


def test_snapshot_freezes_the_default_to_instant(sw):
    first = sw.ada.statement(limit=200)
    token = first["snapshot"]
    p = expect(sw.ada.pay("cy", 5), 201).json()
    fresh_ids = [e["payment"]["payment_id"] for e in sw.ada.statement(limit=200)["entries"]]
    old_ids = [e["payment"]["payment_id"]
               for e in sw.ada.statement(snapshot=token, limit=200)["entries"]]
    assert p["payment_id"] in fresh_ids and p["payment_id"] not in old_ids


@pytest.mark.parametrize("extra", [{"from": "2026-01-01T00:00:00+00:00"},
                                   {"to": "2026-01-01T00:00:00+00:00"},
                                   {"known_at": "2026-01-01T00:00:00+00:00"},
                                   {"from": "garbage"}])
def test_only_limit_and_offset_may_accompany_a_snapshot(sw, extra):
    token = sw.ada.statement(limit=1)["snapshot"]
    expect_error(sw.ada.get("/statement", params={"snapshot": token, **extra}), 422,
                 "validation_failed")


def test_snapshot_with_unrecognised_params_is_fine(sw):
    token = sw.ada.statement(limit=1)["snapshot"]
    expect(sw.ada.get("/statement", params={"snapshot": token, "zzz": "1", "limit": 3}), 200)


def test_snapshot_limit_and_offset_are_validated(sw):
    token = sw.ada.statement(limit=1)["snapshot"]
    for bad in ({"limit": 0}, {"limit": 201}, {"offset": -1}, {"limit": "x"}):
        expect_error(sw.ada.get("/statement", params={"snapshot": token, **bad}), 422,
                     "validation_failed")


def test_unknown_foreign_and_pre_reset_tokens_are_404(sw, reset):
    token = sw.ada.statement(limit=1)["snapshot"]
    expect_error(sw.ada.get("/statement", params={"snapshot": "nope"}), 404, "not_found")
    expect_error(sw.ada.get("/statement", params={"snapshot": token + "x"}), 404, "not_found")
    expect_error(sw.bob.get("/statement", params={"snapshot": token}), 404, "not_found")
    reset(sw.fx)
    ada = sw.new_client("ada")
    expect_error(ada.get("/statement", params={"snapshot": token}), 404, "not_found")


def test_snapshot_needs_a_token(sw, api):
    token = sw.ada.statement(limit=1)["snapshot"]
    expect_error(api().get("/statement", params={"snapshot": token}), 401, "unauthenticated")


def test_snapshot_stays_valid_across_many_reads(sw):
    token = sw.ada.statement(limit=3)["snapshot"]
    want = sw.ada.statement(snapshot=token, limit=200)
    for _ in range(10):
        assert sw.ada.statement(snapshot=token, limit=200) == want


def test_a_windowed_snapshot_keeps_its_window(sw):
    win = {"from": at(0, minutes=450), "to": at(0, minutes=300), "limit": 4}
    first = sw.ada.statement(**win)
    expect(sw.ada.correct("s_020", amount=0, effective_at=at(0, minutes=1)), 201)
    paged = sw.ada.statement(snapshot=first["snapshot"], limit=200)
    unwindowed = sw.ada.statement(**{k: v for k, v in win.items() if k != "limit"}, limit=200)
    assert paged["opening_balance"] == first["opening_balance"]
    assert paged["entries"][:4] == first["entries"]
    assert [e["payment"]["payment_id"] for e in unwindowed["entries"]] != \
        [e["payment"]["payment_id"] for e in paged["entries"]] or len(paged["entries"]) > 0
