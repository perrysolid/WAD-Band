"""Corrections, revisions, known_at (stage-3 'Effective time, recorded time, and corrections').

Error precedence follows the architect's DECISION: body parse 400 -> wrong JSON type 400 -> 401
-> missing key 400 -> claimed key (replay / 409 reuse) -> field validation 422 -> 404 -> 403 ->
422 linked_payment_immutable -> 409 stale_revision -> 409 insufficient_funds ->
409 historical_overdraft.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

import pf_model as m
from pf_client import expect, expect_error, expect_one_of, new_key
from hist import at, replay


def eff(days=0, **kw) -> str:
    return at(days, **kw)


def pids(st) -> list[str]:
    return [e["payment"]["payment_id"] for e in st["entries"]]


# ---- the happy path -----------------------------------------------------------------------

def test_decrease_returns_a_revision_and_moves_the_difference_back(hw):
    r = expect(hw.ada.correct("p_1", expected_revision=1, amount=300, effective_at=eff(5),
                              reason="corrected amount"), 201).json()
    assert r["payment_id"] == "p_1" and r["revision"] == 2 and r["amount"] == 300
    assert m.parse(r["effective_at"]) == m.parse(eff(5)) and r["reason"] == "corrected amount"
    m.assert_d35(r["recorded_at"])
    assert abs((m.parse(r["recorded_at"]) - m.now()).total_seconds()) < 10
    assert hw.ada.balance() == 10_200 and hw.bob.balance() == 2_300   # bob (receiver) gives back
    assert hw.cy.balance() == 500
    hw.oracle()


def test_increase_debits_the_original_sender(hw):
    expect(hw.bob.correct("p_2", amount=350, effective_at=eff(3)), 201)   # bob -> ada 200 => 350
    assert hw.bob.balance() == 2_500 - 150 and hw.ada.balance() == 10_000 + 150
    hw.oracle()


def test_zero_amount_reverses_the_whole_payment(hw):
    expect(hw.ada.correct("p_1", amount=0, effective_at=eff(5)), 201)
    assert hw.ada.balance() == 10_500 and hw.bob.balance() == 2_000
    st = hw.ada.statement()
    ent = [e for e in st["entries"] if e["payment"]["payment_id"] == "p_1"]
    assert len(ent) == 1 and ent[0]["delta"] == 0 and ent[0]["payment"]["amount"] == 0
    assert ent[0]["revision"] == 2
    hw.oracle()


def test_correction_keeps_parties_and_visibility(hw):
    expect(hw.cy.correct("p_3", amount=50, effective_at=eff(1)), 201)
    e = [x for x in hw.cy.statement()["entries"] if x["payment"]["payment_id"] == "p_3"][0]
    p = e["payment"]
    assert (p["from_handle"], p["to_handle"], p["visibility"]) == ("cy", "bob", "private")
    assert p["amount"] == 50
    assert "p_3" not in [x["payment_id"] for x in hw.ada.feed()]      # still private


def test_original_payment_and_receipt_never_change(hw):
    k = new_key()
    body = {"to_handle": "bob", "amount": 400, "note": "n", "visibility": "public"}
    first = expect(hw.ada.post("/payments", json=body, key=k), 201).json()
    expect(hw.ada.correct(first["payment_id"], amount=100, effective_at=first["created_at"]), 201)
    assert expect(hw.ada.post("/payments", json=body, key=k), 200).json() == first
    feed = [p for p in hw.bob.feed() if p["payment_id"] == first["payment_id"]]
    assert feed == [first], "activity keeps showing the original payment"
    assert len(hw.bob.feed()) == 4, "a correction is not a new feed payment"
    hw.oracle()


def test_same_amount_correction_is_allowed_and_adds_a_revision(hw):
    r = expect(hw.ada.correct("p_1", amount=500, effective_at=eff(5)), 201).json()
    assert r["revision"] == 2 and hw.ada.balance() == 10_000
    assert len(hw.ada.revisions("p_1")) == 2


def test_revision_numbers_increment_and_recorded_times_strictly_increase(hw):
    for i in range(6):
        expect(hw.ada.correct("p_1", expected_revision=i + 1, amount=100 + i,
                              effective_at=eff(5)), 201)
    revs = hw.ada.revisions("p_1")
    assert [r["revision"] for r in revs] == list(range(1, 8))
    stamps = [m.parse(r["recorded_at"]) for r in revs]
    assert all(a < b for a, b in zip(stamps, stamps[1:])), stamps


def test_revisions_endpoint_shape_and_revision_one(hw):
    expect(hw.ada.correct("p_1", amount=300, effective_at=eff(4), reason="why"), 201)
    revs = hw.bob.revisions("p_1")                       # the receiver may read it too
    assert len(revs) == 2
    r1, r2 = revs
    assert r1["revision"] == 1 and r1["amount"] == 500 and r1["reason"] == ""
    assert m.parse(r1["effective_at"]) == m.parse(r1["recorded_at"]) == m.parse(at(5))
    assert r2["revision"] == 2 and r2["amount"] == 300 and r2["reason"] == "why"
    for r in revs:
        assert {"revision", "amount", "effective_at", "recorded_at", "reason"} <= set(r)
        m.assert_d35(r["effective_at"]), m.assert_d35(r["recorded_at"])


def test_revisions_only_for_the_two_parties(hw):
    expect(hw.ada.get("/payments/p_1/revisions"), 200)
    expect(hw.bob.get("/payments/p_1/revisions"), 200)
    expect_error(hw.cy.get("/payments/p_1/revisions"), 404, "not_found")     # public, third party
    expect_error(hw.ada.get("/payments/p_999/revisions"), 404, "not_found")
    expect_error(hw.new_client("ada").request("GET", "/payments/p_1/revisions", token=None), 401,
                 "unauthenticated")


def test_revisions_of_a_new_api_payment_start_at_its_created_at(hw):
    p = expect(hw.ada.pay("bob", 9), 201).json()
    revs = hw.ada.revisions(p["payment_id"])
    assert len(revs) == 1 and revs[0]["revision"] == 1 and revs[0]["reason"] == ""
    assert m.parse(revs[0]["effective_at"]) == m.parse(revs[0]["recorded_at"]) == m.parse(p["created_at"])


# ---- validation, ownership, precedence ----------------------------------------------------

BODY = {"expected_revision": 1, "amount": 400, "reason": "r"}


def _post(c, pid, body, key=None):
    return c.post(f"/payments/{pid}/corrections", json=body, key=key or new_key())


def _good(**kw):
    return {**BODY, "effective_at": eff(5), **kw}


@pytest.mark.parametrize("field", ["expected_revision", "amount", "effective_at", "reason"])
def test_every_field_is_required(hw, field):
    body = _good()
    del body[field]
    expect_error(_post(hw.ada, "p_1", body), 422, "validation_failed")
    assert len(hw.ada.revisions("p_1")) == 1


@pytest.mark.parametrize("rev", [0, -1, 1.5, "1", True, None, 10 ** 30])
def test_bad_expected_revision(hw, rev):
    resp = _post(hw.ada, "p_1", _good(expected_revision=rev))
    expect_one_of(resp, (422, "validation_failed"), (400, "malformed_request"))
    assert len(hw.ada.revisions("p_1")) == 1


@pytest.mark.parametrize("amt", [-1, 1_000_000_001, 1.5, "5", True, None, 1e30])
def test_bad_amount_is_422(hw, amt):
    expect_error(_post(hw.ada, "p_1", _good(amount=amt)), 422, "validation_failed")


@pytest.mark.parametrize("amt", [400.0, "4e2"])
def test_integral_amount_forms(hw, amt):
    resp = _post(hw.ada, "p_1", _good(amount=amt if amt != "4e2" else 4e2))
    expect(resp, 201)


@pytest.mark.parametrize("reason", ["", "x" * 201, 5, None, True])
def test_bad_reason(hw, reason):
    expect_one_of(_post(hw.ada, "p_1", _good(reason=reason)),
                  (422, "validation_failed"), (400, "malformed_request"))


def test_reason_length_boundary(hw):
    expect(_post(hw.ada, "p_1", _good(reason="x" * 200)), 201)
    r = hw.ada.revisions("p_1")[-1]
    assert r["reason"] == "x" * 200
    expect(_post(hw.ada, "p_1", _good(expected_revision=2, reason="é" * 200)), 201)
    expect_error(_post(hw.ada, "p_1", _good(expected_revision=3, reason="é" * 201)), 422,
                 "validation_failed")


@pytest.mark.parametrize("when", ["2026-09-24", "2026-09-24T10:00:00", "", "tomorrow", 5, None,
                                  "2026-09-24T10:00:00+0000"])
def test_bad_effective_at(hw, when):
    expect_one_of(_post(hw.ada, "p_1", _good(effective_at=when)),
                  (422, "validation_failed"), (400, "malformed_request"))
    assert len(hw.ada.revisions("p_1")) == 1


def test_effective_at_in_the_future_is_422(hw):
    future = m.iso(m.now() + timedelta(minutes=5))
    expect_error(_post(hw.ada, "p_1", _good(effective_at=future)), 422, "validation_failed")
    far = "2999-01-01T00:00:00+00:00"
    expect_error(_post(hw.ada, "p_1", _good(effective_at=far)), 422, "validation_failed")


def test_effective_at_now_is_accepted_and_any_offset_is_fine(hw):
    expect(_post(hw.ada, "p_1", _good(effective_at=m.iso(m.now(), 330))), 201)


def test_effective_at_may_precede_the_payment(hw):
    r = expect(_post(hw.bob, "p_2", _good(expected_revision=1, amount=100,
                                          effective_at=eff(40))), 201).json()
    assert m.parse(r["effective_at"]) == m.parse(eff(40))


def test_unknown_fields_are_ignored_and_body_must_be_an_object(hw):
    expect(_post(hw.ada, "p_1", _good(parties="x", visibility="private", zzz=1)), 201)
    assert hw.ada.revisions("p_1")[0]["amount"] == 500
    for raw in ("[]", "5", "null", '"x"'):
        resp = hw.ada.post("/payments/p_1/corrections", content=raw, key=new_key())
        expect_one_of(resp, (400, "malformed_request"), (422, "validation_failed"))
    expect_error(hw.ada.post("/payments/p_1/corrections", content="{", key=new_key()), 400,
                 "malformed_request")


def test_only_the_sender_may_correct(hw):
    expect_error(_post(hw.bob, "p_1", _good()), 403, "forbidden")      # the receiver
    expect_error(_post(hw.cy, "p_1", _good()), 403, "forbidden")       # a third party
    expect_error(_post(hw.ada, "p_2", _good()), 403, "forbidden")      # ada is p_2's receiver
    assert len(hw.ada.revisions("p_1")) == 1 and hw.ada.balance() == 10_000


def test_unknown_payment_is_404(hw):
    expect_error(_post(hw.ada, "p_nope", _good()), 404, "not_found")
    expect_error(_post(hw.ada, "x" * 200, _good()), 404, "not_found")


def test_no_token_and_missing_key(hw, api):
    expect_error(api().post("/payments/p_1/corrections", json=_good(), key=new_key()), 401,
                 "unauthenticated")
    expect_error(hw.ada.post("/payments/p_1/corrections", json=_good()), 400,
                 "missing_idempotency_key")
    expect_error(hw.ada.post("/payments/p_1/corrections", json=_good(), key=""), 400,
                 "missing_idempotency_key")
    expect_error(hw.ada.post("/payments/p_1/corrections", json=_good(), key="k" * 256), 422,
                 "validation_failed")
    expect(hw.ada.post("/payments/p_1/corrections", json=_good(), key="k" * 255), 201)


def test_precedence_validation_before_lookup_and_permission(hw):
    bad = _good(amount=-5)
    expect_error(_post(hw.ada, "p_nope", bad), 422, "validation_failed")      # before 404
    expect_error(_post(hw.cy, "p_1", bad), 422, "validation_failed")          # before 403
    expect_error(_post(hw.cy, "p_nope", _good()), 404, "not_found")
    expect_error(_post(hw.cy, "p_1", _good(expected_revision=9)), 403, "forbidden")   # 403 < stale


def test_a_claimed_key_beats_everything(hw):
    k = new_key()
    first = expect(_post(hw.ada, "p_1", _good(), key=k), 201).json()
    expect_error(_post(hw.ada, "p_1", _good(amount=-5), key=k), 409, "idempotency_key_reuse")
    expect_error(_post(hw.ada, "p_1", _good(amount=401), key=k), 409, "idempotency_key_reuse")
    assert expect(_post(hw.ada, "p_1", _good(), key=k), 200).json() == first


def test_stale_revision(hw):
    expect(_post(hw.ada, "p_1", _good(amount=100)), 201)
    expect_error(_post(hw.ada, "p_1", _good(expected_revision=1)), 409, "stale_revision")
    expect_error(_post(hw.ada, "p_1", _good(expected_revision=7)), 409, "stale_revision")
    assert len(hw.ada.revisions("p_1")) == 2
    expect(_post(hw.ada, "p_1", _good(expected_revision=2, amount=200)), 201)


def test_replay_returns_the_original_revision_after_newer_ones(hw):
    k = new_key()
    first = expect(_post(hw.ada, "p_1", _good(amount=100), key=k), 201).json()
    expect(_post(hw.ada, "p_1", _good(expected_revision=2, amount=200)), 201)
    expect(_post(hw.ada, "p_1", _good(expected_revision=3, amount=300)), 201)
    again = expect(_post(hw.ada, "p_1", _good(amount=100), key=k), 200).json()
    assert again == first and again["revision"] == 2
    assert len(hw.ada.revisions("p_1")) == 4
    hw.oracle()


def test_key_reuse_with_a_different_body_is_409(hw):
    k = new_key()
    expect(_post(hw.ada, "p_1", _good(), key=k), 201)
    for change in ({"amount": 401}, {"reason": "other"}, {"expected_revision": 2},
                   {"effective_at": eff(4)}):
        expect_error(_post(hw.ada, "p_1", _good(**change), key=k), 409, "idempotency_key_reuse")
    expect_error(_post(hw.ada, "p_2", _good(), key=k), 403, "forbidden")      # other path/id: new request
    assert len(hw.ada.revisions("p_1")) == 2


def test_keys_are_per_user_and_failed_keys_are_reusable(hw):
    k = new_key()
    expect_error(_post(hw.bob, "p_1", _good(), key=k), 403, "forbidden")
    expect(_post(hw.ada, "p_1", _good(), key=k), 201)                          # same key, other user
    k2 = new_key()
    expect_error(_post(hw.ada, "p_1", _good(expected_revision=9), key=k2), 409, "stale_revision")
    expect(_post(hw.ada, "p_1", _good(expected_revision=2, amount=1), key=k2), 201)


# ---- funds: insufficient vs historical overdraft ---------------------------------------------

def snapshot_of(w):
    return {"bal": w.balances(), "st": {h: c.statement_all() for h, c in w.clients.items()},
            "revs": {p: w.ada.revisions(p) for p in ("q1", "q2")}}


def strip(s):
    for st in s["st"].values():
        st.pop("snapshot", None)
    return s


def test_current_debit_unaffordable_is_insufficient_funds(tw):
    before = strip(snapshot_of(tw))
    k = new_key()
    expect_error(_post(tw.ada, "q2", _good(amount=2_000, effective_at=eff(3)), key=k), 409,
                 "insufficient_funds")          # also a historical overdraft: insufficient wins
    assert strip(snapshot_of(tw)) == before


def test_historical_overdraft_when_an_amount_shrinks(tw):
    before = strip(snapshot_of(tw))
    k = new_key()
    body = _good(amount=100, effective_at=eff(5))     # ada affordable now (900 <= 1000)
    expect_error(_post(tw.cy, "q1", body, key=k), 409, "historical_overdraft")
    assert strip(snapshot_of(tw)) == before
    assert len(tw.cy.revisions("q1")) == 1
    # the failed key is reusable; a valid correction under it succeeds exactly once
    ok = _good(amount=900, effective_at=eff(5))
    r = expect(_post(tw.cy, "q1", ok, key=k), 201).json()
    assert r["revision"] == 2
    tw.oracle()


def test_historical_overdraft_when_the_effective_time_moves(tw):
    before = strip(snapshot_of(tw))
    # q1 (cy -> ada 1000) moved to after q2 (ada -> bob 900): ada would be at -900 at -3d
    expect_error(_post(tw.cy, "q1", _good(amount=1_000, effective_at=eff(1)), key=new_key()), 409,
                 "historical_overdraft")
    assert strip(snapshot_of(tw)) == before


def test_an_overdraft_free_move_is_accepted(tw):
    expect(_post(tw.cy, "q1", _good(amount=1_000, effective_at=eff(4)), key=new_key()), 201)
    tw.oracle()
    assert [e["payment"]["payment_id"] for e in tw.ada.statement()["entries"]] == ["q1", "q2", "q3"]


def test_balances_at_one_instant_combine_before_the_boundary_is_judged(make_world):
    t = at(2)
    fx = m.history_fixture([m.seeded_payment("pa", "cy", "ada", 100, t),
                            m.seeded_payment("pb", "ada", "bob", 100, t)],
                           ending={"ada": 0, "bob": 100, "cy": 0})
    w = make_world(fx)
    assert m.openings(fx)["ada"] == 0
    assert w.ada.me_at(as_of=t)["balance"] == 0
    assert w.ada.me_at(as_of=m.iso(m.parse(t) - timedelta(seconds=1)))["balance"] == 0
    expect(_post(w.ada, "pb", _good(amount=100, effective_at=t)), 201)       # no-op: still combined
    expect(_post(w.cy, "pa", _good(amount=100, effective_at=t)), 201)
    w.oracle()


def test_historical_overdraft_is_judged_against_available_too(make_world):
    """A hold that existed at the boundary counts: total minus held may not go negative."""
    fx = m.history_fixture([m.seeded_payment("q1", "cy", "ada", 1_000, at(5)),
                            m.seeded_payment("q3", "ada", "bob", 400, at(1))],
                           ending={"ada": 600, "bob": 400, "cy": 0},
                           authorizations=[m.hold("a1", "ada", "cy", 500, created_at=at(2))])
    w = make_world(fx)
    me = w.ada.me()
    assert (me["total"], me["held"], me["available"]) == (600, 500, 100)
    # shrink q1 so that at -1d ada's total (100... ) falls below its 500 hold
    resp = _post(w.cy, "q1", _good(amount=800, effective_at=eff(5)))
    expect_one_of(resp, (409, "historical_overdraft"), (409, "insufficient_funds"))
    assert w.ada.me()["total"] == 600


# ---- linked payments --------------------------------------------------------------------------

def test_settlement_members_cannot_be_corrected(op_world):
    w = op_world
    s = expect(w.ada.settle([{"from_handle": "ada", "to_handle": "bob", "amount": 10},
                             {"from_handle": "bob", "to_handle": "cy", "amount": 5}]), 201).json()
    for p, sender in zip(s["payments"], (w.ada, w.bob)):
        expect_error(sender.correct(p["payment_id"], amount=1, effective_at=p["created_at"]),
                     422, "linked_payment_immutable")
        assert len(sender.revisions(p["payment_id"])) == 1
    expect_error(w.cy.correct(s["payments"][0]["payment_id"], amount=1), 403, "forbidden")
    expect_error(w.ada.correct(s["payments"][0]["payment_id"], expected_revision=9, amount=1,
                               effective_at=s["payments"][0]["created_at"]), 422,
                 "linked_payment_immutable")                       # before stale_revision
    r = w.ada.revisions(s["payments"][0]["payment_id"])
    assert m.parse(r[0]["effective_at"]) == m.parse(r[0]["recorded_at"]) == m.parse(s["committed_at"])
    w.oracle()


def test_captures_cannot_be_corrected(world):
    a = expect(world.ada.authorize("bob", 500), 201).json()
    c = expect(world.bob.capture(a["authorization_id"], {"amount": 200, "final": False}), 201).json()
    expect_error(world.ada.correct(c["payment_id"], amount=1), 422, "linked_payment_immutable")
    expect_error(world.bob.correct(c["payment_id"], amount=1), 403, "forbidden")
    c2 = expect(world.bob.capture(a["authorization_id"]), 201).json()
    expect_error(world.ada.correct(c2["payment_id"], amount=1), 422, "linked_payment_immutable")
    assert len(world.ada.revisions(c["payment_id"])) == 1
    world.oracle()


def test_request_payments_and_plain_payments_are_correctable(world):
    r = expect(world.bob.ask("ada", 300), 201).json()
    p = expect(world.ada.pay_request(r["request_id"]), 201).json()
    expect(world.ada.correct(p["payment_id"], amount=100, effective_at=p["created_at"]), 201)
    assert world.ada.balance() == 10_000 - 100 and world.bob.balance() == 2_600
    # the request keeps its original payment_id and paid status
    assert [x for x in world.bob.requests_list() if x["request_id"] == r["request_id"]][0][
        "payment_id"] == p["payment_id"]
    world.oracle()


# ---- known_at ----------------------------------------------------------------------------------

def test_known_at_selects_the_latest_revision_recorded_at_or_before(world):
    p = expect(world.ada.pay("bob", 400), 201).json()
    c1 = p["created_at"]
    r2 = expect(world.ada.correct(p["payment_id"], amount=100, effective_at=c1), 201).json()
    assert m.parse(r2["recorded_at"]) > m.parse(c1)
    cur = world.ada.me()["balance"]
    assert cur == 10_000 - 100
    assert world.ada.me_at(known_at=c1)["balance"] == 10_000 - 400            # inclusive
    assert world.ada.me_at(known_at=r2["recorded_at"])["balance"] == cur      # inclusive
    far = world.ada.me_at(known_at="2999-01-01T00:00:00+00:00")
    assert far["balance"] == cur and far["known_at"] == "2999-01-01T00:00:00+00:00"
    before = m.iso(m.parse(c1) - timedelta(seconds=1))
    me = world.ada.me_at(known_at=before)
    assert me["balance"] == 10_000, "not yet recorded: contributes nothing"
    assert me["known_at"] == before and "as_of" not in me


def test_known_at_in_a_statement(world):
    p = expect(world.ada.pay("bob", 400), 201).json()
    r2 = expect(world.ada.correct(p["payment_id"], amount=100, effective_at=p["created_at"]),
                201).json()
    old = world.ada.statement(known_at=p["created_at"])
    new = world.ada.statement()
    assert [e["revision"] for e in old["entries"]] == [1] and old["entries"][0]["delta"] == -400
    assert old["entries"][0]["payment"]["amount"] == 400
    assert [e["revision"] for e in new["entries"]] == [2] and new["entries"][0]["delta"] == -100
    assert m.parse(new["entries"][0]["recorded_at"]) == m.parse(r2["recorded_at"])
    assert old["closing_balance"] == 9_600 and new["closing_balance"] == 9_900
    none_yet = world.ada.statement(known_at=m.iso(m.parse(p["created_at"]) - timedelta(seconds=5)))
    assert none_yet["entries"] == [] and none_yet["closing_balance"] == 10_000
    world.oracle()


def test_known_at_with_as_of_and_future_values(hw):
    p = expect(hw.ada.pay("bob", 77), 201).json()
    expect(hw.ada.correct(p["payment_id"], amount=7, effective_at=p["created_at"]), 201)
    far = "2999-01-01T00:00:00+00:00"
    me = hw.ada.me_at(as_of=far, known_at=far)
    assert me["balance"] == 9_993 and me["as_of"] == far and me["known_at"] == far
    old = hw.ada.me_at(as_of=far, known_at=p["created_at"])
    assert old["balance"] == 10_000 - 77


def test_correction_moves_a_payment_into_and_out_of_a_window(hw):
    # p_1 is at -5d. Re-date it to -1h: it leaves [-6d, -4d) and joins [-2h, now]
    old_window = {"from": at(6), "to": at(4)}
    new_window = {"from": at(0, hours=2)}
    assert pids(hw.ada.statement(**old_window)) == ["p_1"]
    expect(hw.ada.correct("p_1", amount=500, effective_at=at(0, hours=1)), 201)
    assert pids(hw.ada.statement(**old_window)) == []
    assert pids(hw.ada.statement(**new_window)) == ["p_1"]
    assert pids(hw.ada.statement(known_at=at(0, minutes=30), **old_window)) == ["p_1"]   # known then
    e = hw.ada.statement(**new_window)["entries"][0]
    assert m.parse(e["effective_at"]) == m.parse(at(0, hours=1)) and e["revision"] == 2
    assert hw.ada.me_at(as_of=at(2))["balance"] == replay(hw.fx, upto=m.parse(at(2)))["ada"] + 500
    assert hw.ada.statement(**old_window)["closing_balance"] == hw.ada.statement(
        **old_window)["opening_balance"]
    hw.oracle()


def test_statement_orders_by_selected_effective_time(hw):
    expect(hw.ada.correct("p_1", amount=500, effective_at=at(2)), 201)   # p_1 now after p_2 (-3d)
    assert pids(hw.ada.statement()) == ["p_2", "p_1"]
    hw.oracle()


def test_historical_views_sum_to_the_seeded_total_after_corrections(hw):
    expect(hw.ada.correct("p_1", amount=100, effective_at=at(4)), 201)
    expect(hw.bob.correct("p_2", amount=900, effective_at=at(2)), 201)
    p = expect(hw.ada.pay("cy", 33), 201).json()
    expect(hw.ada.correct(p["payment_id"], amount=0, effective_at=p["created_at"]), 201)
    probes = [at(40), at(5), at(4), at(3.5), at(3), at(2), at(1), at(0, 0, 1), m.iso(m.now()),
              "2999-01-01T00:00:00+00:00"]
    for known in (None, at(0, minutes=5), "2999-01-01T00:00:00+00:00", at(30)):
        for t in probes:
            params = {"as_of": t, **({"known_at": known} if known else {})}
            assert sum(c.me_at(**params)["balance"] for c in hw.clients.values()) == hw.total, params
    hw.oracle()
