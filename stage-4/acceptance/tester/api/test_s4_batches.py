"""POST /correction-batches (stage-4 'Batch corrections'). Error order (S4-D3): 401, 403 non-operator
(before body), key 400, claimed key, then body shape; items in input order (fields 422, 404,
linked_payment_immutable, stale_revision, refund_exceeds_payment); incomplete_settlement; differing
member instants 422; combined current available 409 insufficient_funds; historical_overdraft 409."""
from __future__ import annotations

import copy
from datetime import timedelta

import pytest

import pf_model as m
from pf_client import expect, expect_error, expect_one_of, new_key


@pytest.fixture
def bw(op_world):
    """ada is the operator. Three ordinary payments and one two-member settlement."""
    w = op_world
    w.p = [expect(w.clients[f].pay(t, a, note=f"n{i}"), 201).json()
           for i, (f, t, a) in enumerate([("ada", "bob", 100), ("bob", "cy", 200), ("cy", "dee", 50)])]
    w.s = expect(w.ada.settle([{"from_handle": "ada", "to_handle": "cy", "amount": 40},
                               {"from_handle": "bob", "to_handle": "dee", "amount": 20}]), 201).json()
    return w


def it(p, amount=0, **kw):
    return m.item(p["payment_id"], amount, at_=kw.pop("at_", p["created_at"]), **kw)


def snap(w):
    return {"bal": w.balances(), "st": {h: [(e["payment"]["payment_id"], e["revision"], e["delta"]) for e in
                                            c.statement_all()["entries"]] for h, c in w.clients.items()},
            "revs": {p["payment_id"]: w.ada.get(f"/payments/{p['payment_id']}/revisions").status_code
                     for p in w.p}}


def test_basic_batch_shape_and_effect(bw):
    items = [it(bw.p[0], 30, reason="a"), it(bw.p[1], 0, reason="b")]
    r = expect(bw.ada.batch(items), 201).json()
    assert r["correction_batch_id"] and isinstance(r["correction_batch_id"], str)
    m.assert_d35(r["recorded_at"])
    revs = r["revisions"]
    assert [x["payment_id"] for x in revs] == [bw.p[0]["payment_id"], bw.p[1]["payment_id"]]
    assert [x["revision"] for x in revs] == [2, 2] and [x["amount"] for x in revs] == [30, 0]
    assert [x["reason"] for x in revs] == ["a", "b"]
    for x in revs:
        assert x["correction_batch_id"] == r["correction_batch_id"]
        assert x["recorded_at"] == r["recorded_at"]
        assert {"payment_id", "revision", "amount", "effective_at", "recorded_at", "reason"} <= set(x)
    # p0: ada->bob 100 down to 30 (bob returns 70); p1: bob->cy 200 reversed
    assert bw.ada.balance() == 10_000 - 100 - 40 + 70 and bw.bob.balance() == 2_500 + 100 - 200 - 20 - 70 + 200
    bw.oracle()


def test_batch_recorded_at_is_after_every_members_previous_recorded_at(bw):
    expect(bw.ada.correct(bw.p[0]["payment_id"], amount=90, effective_at=bw.p[0]["created_at"]), 201)
    last = max(m.parse(x["recorded_at"]) for x in bw.ada.revisions(bw.p[0]["payment_id"]))
    r = expect(bw.ada.batch([it(bw.p[0], 80, rev=2), it(bw.p[1], 150)]), 201).json()
    assert m.parse(r["recorded_at"]) > last
    prev1 = bw.bob.revisions(bw.p[1]["payment_id"])[0]["recorded_at"]
    assert m.parse(r["recorded_at"]) > m.parse(prev1)


def test_batch_id_is_exposed_on_batch_revisions_only(bw):
    r = expect(bw.ada.batch([it(bw.p[0], 30), it(bw.p[1], 0)]), 201).json()
    revs = bw.ada.revisions(bw.p[0]["payment_id"])
    assert revs[1]["correction_batch_id"] == r["correction_batch_id"]
    assert revs[0].get("correction_batch_id") in (None,) or "correction_batch_id" not in revs[0]
    single = expect(bw.ada.correct(bw.p[0]["payment_id"], expected_revision=2, amount=10,
                                   effective_at=bw.p[0]["created_at"]), 201).json()
    assert "correction_batch_id" not in single or single["correction_batch_id"] is None
    r2 = expect(bw.ada.batch([it(bw.p[1], 5, rev=2)]), 201).json()
    assert r2["correction_batch_id"] != r["correction_batch_id"]


def test_statements_reflect_batch_revisions_and_old_snapshots_do_not(bw):
    first = bw.ada.statement(limit=1)
    want = bw.ada.statement(snapshot=first["snapshot"], limit=200)["entries"]
    expect(bw.ada.batch([it(bw.p[0], 0), it(bw.p[1], 0)]), 201)
    assert bw.ada.statement(snapshot=first["snapshot"], limit=200)["entries"] == want
    ent = {e["payment"]["payment_id"]: e for e in bw.ada.statement_all()["entries"]}
    assert ent[bw.p[0]["payment_id"]]["revision"] == 2 and ent[bw.p[0]["payment_id"]]["delta"] == 0
    bw.oracle()


def test_original_receipts_and_feed_are_unchanged(bw):
    k = new_key()
    body = {"to_handle": "cy", "amount": 25}
    first = expect(bw.ada.post("/payments", json=body, key=k), 201).json()
    expect(bw.ada.batch([m.item(first["payment_id"], 5, at_=first["created_at"])]), 201)
    assert expect(bw.ada.post("/payments", json=body, key=k), 200).json() == first
    assert [x for x in bw.ada.feed() if x["payment_id"] == first["payment_id"]] == [first]


def test_the_operator_may_correct_other_users_payments_but_not_captures_or_refunds(bw):
    a = expect(bw.bob.authorize("cy", 100), 201).json()
    c = expect(bw.cy.capture(a["authorization_id"]), 201).json()
    r = expect(bw.cy.refund(bw.p[1]["payment_id"], 10), 201).json()
    for tgt in (c, r):
        expect_error(bw.ada.batch([it(bw.p[0], 1), it(tgt, 1)]), 422, "linked_payment_immutable")
    expect(bw.ada.batch([it(bw.p[1], 150)]), 201)
    rq = expect(bw.cy.ask("dee", 10), 201).json()
    rp = expect(bw.dee.pay_request(rq["request_id"]), 201).json()
    expect(bw.ada.batch([it(rp, 4)]), 201)                            # request payment: allowed
    bw.oracle()


# ---- request-level validation ------------------------------------------------------------------

def test_permissions_and_key(bw, api):
    good = [it(bw.p[0], 1)]
    expect_error(api().post("/correction-batches", json={"corrections": good}, key=new_key()), 401,
                 "unauthenticated")
    expect_error(bw.bob.batch(good), 403, "forbidden")
    expect_error(bw.bob.batch([]), 403, "forbidden")                    # before body validation
    expect_error(bw.ada.post("/correction-batches", json={"corrections": good}), 400,
                 "missing_idempotency_key")
    expect_error(bw.ada.post("/correction-batches", json={"corrections": good}, key=""), 400,
                 "missing_idempotency_key")
    expect_error(bw.ada.post("/correction-batches", json={"corrections": good}, key="k" * 256), 422,
                 "validation_failed")
    expect_error(bw.ada.post("/correction-batches", content="{", key=new_key()), 400, "malformed_request")
    assert bw.ada.revisions(bw.p[0]["payment_id"])[-1]["revision"] == 1


@pytest.mark.parametrize("body", [{}, {"corrections": []}, {"corrections": None}, {"corrections": {}},
                                  {"corrections": "x"}, {"corrections": [1]}, {"corrections": [None]},
                                  {"corrections": [[]]}])
def test_corrections_must_be_a_nonempty_array_of_objects(bw, body):
    expect_one_of(bw.ada.post("/correction-batches", json=body, key=new_key()),
                  (422, "validation_failed"), (400, "malformed_request"))


def test_item_count_bounds(make_world):
    users = [m.user("ada", 1_000_000), m.user("bob", 1_000_000)]
    w = make_world(m.fixture(users, operators=["u_ada"]))
    ps = [expect(w.ada.pay("bob", 1 + i), 201).json() for i in range(33)]
    snapshot_before = w.balances()
    expect_error(w.ada.batch([m.item(p["payment_id"], 0, at_=p["created_at"]) for p in ps]), 422,
                 "validation_failed")                                   # 33
    assert w.balances() == snapshot_before
    r = expect(w.ada.batch([m.item(p["payment_id"], 0, at_=p["created_at"]) for p in ps[:32]]), 201).json()
    assert len(r["revisions"]) == 32
    expect(w.ada.batch([m.item(ps[32]["payment_id"], 0, at_=ps[32]["created_at"])]), 201)   # 1 item
    assert w.ada.balance() == 1_000_000 - 33 * 34 // 2 + sum(1 + i for i in range(33))
    w.oracle()


def test_duplicate_payment_ids_are_422(bw):
    expect_error(bw.ada.batch([it(bw.p[0], 1), it(bw.p[0], 2)]), 422, "validation_failed")
    assert bw.ada.revisions(bw.p[0]["payment_id"])[-1]["revision"] == 1


@pytest.mark.parametrize("change", [{"expected_revision": 0}, {"expected_revision": "1"}, {"amount": -1},
                                    {"amount": 1.5}, {"amount": 1_000_000_001}, {"reason": ""},
                                    {"reason": "x" * 201}, {"effective_at": "2026-01-01"},
                                    {"effective_at": ""}, {"payment_id": ""}])
def test_item_field_validation(bw, change):
    bad = {**it(bw.p[0], 1), **change}
    expect_one_of(bw.ada.batch([bad]), (422, "validation_failed"), (400, "malformed_request"),
                  (404, "not_found"))
    assert bw.ada.revisions(bw.p[0]["payment_id"])[-1]["revision"] == 1


@pytest.mark.parametrize("missing", ["payment_id", "expected_revision", "amount", "effective_at", "reason"])
def test_item_fields_are_required(bw, missing):
    bad = it(bw.p[0], 1)
    del bad[missing]
    expect_error(bw.ada.batch([bad]), 422, "validation_failed")


def test_future_effective_at_is_422(bw):
    far = m.iso(m.now() + timedelta(hours=1))
    expect_error(bw.ada.batch([it(bw.p[0], 1, at_=far)]), 422, "validation_failed")


def test_unknown_fields_are_ignored(bw):
    r = expect(bw.ada.batch([{**it(bw.p[0], 1), "zzz": 1, "visibility": "private"}], zzz={"a": 1}), 201)
    assert r.json()["revisions"][0]["amount"] == 1


# ---- per-item errors in input order ------------------------------------------------------------

def test_item_errors_are_reported_in_input_order(bw):
    stale = it(bw.p[0], 1, rev=9)
    invalid = {**it(bw.p[1], 1), "amount": -3}
    unknown = m.item("p_nope", 1)
    expect_error(bw.ada.batch([stale, invalid]), 409, "stale_revision")
    expect_error(bw.ada.batch([invalid, stale]), 422, "validation_failed")
    expect_error(bw.ada.batch([unknown, invalid]), 404, "not_found")
    expect_error(bw.ada.batch([invalid, unknown]), 422, "validation_failed")
    expect_error(bw.ada.batch([stale, unknown]), 409, "stale_revision")
    expect_error(bw.ada.batch([unknown, stale]), 404, "not_found")
    assert bw.ada.revisions(bw.p[0]["payment_id"])[-1]["revision"] == 1


def test_within_one_item_linked_precedes_stale(bw):
    a = expect(bw.bob.authorize("cy", 10), 201).json()
    c = expect(bw.cy.capture(a["authorization_id"]), 201).json()
    expect_error(bw.ada.batch([it(c, 1, rev=9)]), 422, "linked_payment_immutable")


def test_refund_exceeds_is_an_item_error(bw):
    expect(bw.cy.refund(bw.p[1]["payment_id"], 120), 201)
    expect_error(bw.ada.batch([it(bw.p[1], 119)]), 422, "refund_exceeds_payment")
    expect_error(bw.ada.batch([it(bw.p[0], 5), it(bw.p[1], 119)]), 422, "refund_exceeds_payment")
    expect(bw.ada.batch([it(bw.p[1], 120)]), 201)


def test_item_errors_precede_settlement_completeness(bw):
    one_member = it(bw.s["payments"][0], 0, at_=bw.s["committed_at"])
    expect_error(bw.ada.batch([one_member, m.item("p_nope", 1)]), 404, "not_found")
    expect_error(bw.ada.batch([one_member, it(bw.p[0], 1, rev=7)]), 409, "stale_revision")


# ---- settlement members --------------------------------------------------------------------------

def members(bw, amounts=(0, 0), at_=None, rev=1):
    return [m.item(p["payment_id"], a, at_=at_ or bw.s["committed_at"], rev=rev)
            for p, a in zip(bw.s["payments"], amounts)]


def test_correcting_all_members_of_a_settlement(bw):
    bals = bw.balances()
    r = expect(bw.ada.batch(members(bw, (0, 5))), 201).json()
    assert [x["amount"] for x in r["revisions"]] == [0, 5]
    assert bw.ada.balance() == bals["ada"] + 40 and bw.cy.balance() == bals["cy"] - 40
    assert bw.bob.balance() == bals["bob"] + 15 and bw.dee.balance() == bals["dee"] - 15
    feed = [p for p in bw.ada.feed() if p["settlement_id"] == bw.s["settlement_id"]]
    assert sorted(x["payment_id"] for x in feed) == sorted(x["payment_id"] for x in bw.s["payments"])
    assert [p["amount"] for p in bw.s["payments"]] == [40, 20]       # original receipts unchanged
    bw.oracle()


def test_a_partial_settlement_is_incomplete(bw):
    before = snap(bw)
    k = new_key()
    expect_error(bw.ada.batch(members(bw)[:1], key=k), 422, "incomplete_settlement")
    expect_error(bw.ada.batch(members(bw)[1:]), 422, "incomplete_settlement")
    expect_error(bw.ada.batch([it(bw.p[0], 1), *members(bw)[:1]]), 422, "incomplete_settlement")
    assert snap(bw) == before
    expect(bw.ada.batch(members(bw), key=k), 201)                          # the failed key is reusable


def test_members_must_share_one_effective_instant(bw):
    t = m.parse(bw.s["committed_at"])
    items = members(bw)
    items[1]["effective_at"] = m.iso(t - timedelta(seconds=1))
    before = snap(bw)
    expect_error(bw.ada.batch(items), 422, "validation_failed")
    assert snap(bw) == before


def test_equal_instants_in_different_offset_spellings_are_accepted(bw):
    t = m.parse(bw.s["committed_at"])
    items = members(bw)
    items[0]["effective_at"] = m.iso(t, 0)
    items[1]["effective_at"] = m.iso(t, 330)
    expect(bw.ada.batch(items), 201)
    bw.oracle()


def test_members_may_move_to_a_common_earlier_instant(bw):
    t = m.iso(m.parse(bw.s["committed_at"]) - timedelta(hours=1))
    expect(bw.ada.batch(members(bw, (10, 10), at_=t)), 201)
    revs = bw.ada.revisions(bw.s["payments"][0]["payment_id"])
    assert m.parse(revs[1]["effective_at"]) == m.parse(t)
    bw.oracle()


def test_members_of_two_settlements_may_use_different_instants(bw):
    s2 = expect(bw.ada.settle([{"from_handle": "ada", "to_handle": "dee", "amount": 3}]), 201).json()
    items = members(bw) + [m.item(s2["payments"][0]["payment_id"], 0, at_=s2["committed_at"])]
    items[2]["effective_at"] = m.iso(m.parse(s2["committed_at"]) - timedelta(minutes=1))
    expect(bw.ada.batch(items), 201)
    bw.oracle()


def test_single_correction_of_a_member_stays_immutable_and_nonmembers_stay_correctable(bw):
    mem = bw.s["payments"][0]
    expect_error(bw.ada.correct(mem["payment_id"], amount=1, effective_at=mem["created_at"]), 422,
                 "linked_payment_immutable")
    expect(bw.ada.correct(bw.p[0]["payment_id"], amount=1, effective_at=bw.p[0]["created_at"]), 201)


def test_a_refunded_member_cannot_drop_below_the_refund(bw):
    mem = bw.s["payments"][0]
    expect(bw.cy.refund(mem["payment_id"], 30), 201)
    expect_error(bw.ada.batch(members(bw, (29, 0))), 422, "refund_exceeds_payment")
    expect(bw.ada.batch(members(bw, (30, 0))), 201)
    feed = [p for p in bw.ada.feed() if p["settlement_id"] == bw.s["settlement_id"]]
    assert len(feed) == 2, "refunds never change settlement membership"


# ---- funds ---------------------------------------------------------------------------------------

def test_combined_affordability_is_judged_on_the_whole_batch(make_world):
    users = [m.user("ada", 100), m.user("bob", 100), m.user("cy", 0), m.user("dee", 0)]
    w = make_world(m.fixture(users, operators=["u_ada"]))
    p1 = expect(w.bob.pay("cy", 50), 201).json()
    p2 = expect(w.bob.pay("dee", 50), 201).json()
    assert w.bob.balance() == 0
    # each +50 alone is unaffordable for bob (0 left); combined also; but a reversal pays for itself:
    expect_error(w.ada.batch([m.item(p1["payment_id"], 100, at_=p1["created_at"])]), 409,
                 "insufficient_funds")
    r = expect(w.ada.batch([m.item(p1["payment_id"], 100, at_=p1["created_at"]),
                            m.item(p2["payment_id"], 0, at_=p2["created_at"])]), 201).json()
    assert len(r["revisions"]) == 2                                   # net +0 for bob: combined effect
    assert w.bob.balance() == 0 - 50 + 50
    w.oracle()


def test_insufficient_funds_rejects_the_whole_batch(make_world):
    users = [m.user("ada", 0), m.user("bob", 120), m.user("cy", 0), m.user("dee", 0)]
    w = make_world(m.fixture(users, operators=["u_ada"]))
    p1 = expect(w.bob.pay("cy", 50), 201).json()
    p2 = expect(w.bob.pay("dee", 50), 201).json()
    before = (w.balances(), w.bob.revisions(p1["payment_id"]))
    k = new_key()
    items = [m.item(p1["payment_id"], 80, at_=p1["created_at"]), m.item(p2["payment_id"], 80, at_=p2["created_at"])]
    expect_error(w.ada.batch(items, key=k), 409, "insufficient_funds")          # +60 needed, 20 left
    assert (w.balances(), w.bob.revisions(p1["payment_id"])) == before
    items[1]["amount"] = 60                                                       # +40 total: ok? 20 left -> no
    expect_error(w.ada.batch(items, key=new_key()), 409, "insufficient_funds")
    items[0]["amount"] = 60
    expect(w.ada.batch(items, key=new_key()), 201)                                # +20 total == available
    assert w.bob.balance() == 0
    w.oracle()


def test_funds_use_available_not_total(make_world):
    users = [m.user("ada", 0), m.user("bob", 200), m.user("cy", 0)]
    w = make_world(m.fixture(users, operators=["u_ada"]))
    p = expect(w.bob.pay("cy", 50), 201).json()
    expect(w.bob.authorize("cy", 150), 201)                                      # bob available 0
    expect_error(w.ada.batch([m.item(p["payment_id"], 60, at_=p["created_at"])]), 409, "insufficient_funds")


def test_insufficient_funds_precedes_historical_overdraft(make_world):
    users = [m.user("ada", 0), m.user("bob", 10), m.user("cy", 0)]
    w = make_world(m.history_fixture([m.seeded_payment("q1", "cy", "bob", 1_000, "2020-01-01T00:00:00+00:00"),
                                      m.seeded_payment("q2", "bob", "ada", 990, "2020-01-02T00:00:00+00:00")],
                                     ending={"ada": 990, "bob": 10, "cy": 0}, operators=["u_ada"]))
    # shrinking q1 to 0: bob (10) cannot give back 1000 -> insufficient_funds (current), not historical
    expect_error(w.ada.batch([m.item("q1", 0, at_="2020-01-01T00:00:00+00:00")]), 409, "insufficient_funds")
    del users


def test_historical_overdraft(make_world):
    fx = m.history_fixture([m.seeded_payment("q1", "cy", "ada", 1_000, "2020-01-01T00:00:00+00:00"),
                            m.seeded_payment("q2", "ada", "bob", 900, "2020-01-02T00:00:00+00:00"),
                            m.seeded_payment("q3", "bob", "ada", 900, "2020-01-03T00:00:00+00:00")],
                           ending={"ada": 1_000, "bob": 0, "cy": 0}, operators=["u_ada"])
    w = make_world(fx)
    before = {h: c.statement_all()["entries"] for h, c in w.clients.items()}
    k = new_key()
    expect_error(w.ada.batch([m.item("q1", 100, at_="2020-01-01T00:00:00+00:00")], key=k), 409,
                 "historical_overdraft")
    assert {h: c.statement_all()["entries"] for h, c in w.clients.items()} == before
    expect(w.ada.batch([m.item("q1", 1_000, at_="2020-01-01T00:00:00+00:00")], key=k), 201)
    w.oracle()


# ---- idempotency, concurrency ----------------------------------------------------------------------

def test_replay_returns_the_original_batch_response(bw):
    k = new_key()
    items = [it(bw.p[0], 30), it(bw.p[1], 0)]
    first = expect(bw.ada.batch(items, key=k), 201).json()
    expect(bw.ada.correct(bw.p[0]["payment_id"], expected_revision=2, amount=7,
                          effective_at=bw.p[0]["created_at"]), 201)
    again = expect(bw.ada.batch(items, key=k), 200).json()
    assert again == first
    assert bw.ada.balance() == 10_000 - 100 - 40 + 93 and len(bw.ada.revisions(bw.p[0]["payment_id"])) == 3
    changed = copy.deepcopy(items)
    changed[0]["amount"] = 31
    expect_error(bw.ada.batch(changed, key=k), 409, "idempotency_key_reuse")
    expect_error(bw.ada.batch(items[::-1], key=k), 409, "idempotency_key_reuse")
    expect_error(bw.ada.batch([{**items[0], "amount": -1}], key=k), 409, "idempotency_key_reuse")
    bw.oracle()


def test_a_rejected_batch_claims_no_key_and_leaves_ids_unchanged(bw):
    k = new_key()
    expect_error(bw.ada.batch([it(bw.p[0], 1, rev=9)], key=k), 409, "stale_revision")
    ok = expect(bw.ada.batch([it(bw.p[0], 1)], key=k), 201).json()
    assert ok["revisions"][0]["revision"] == 2


def test_concurrent_batches_sharing_an_expected_revision_one_wins(bw):
    res = m.burst(lambda i: bw.ada.batch([it(bw.p[0], i), it(bw.p[1], 5 + i)]), 30)
    m.assert_no_5xx(res)
    assert [r.status_code for r in res].count(201) == 1, m.codes(res)
    assert all(r.json()["error"]["code"] == "stale_revision" for r in res if r.status_code != 201)
    assert len(bw.ada.revisions(bw.p[0]["payment_id"])) == 2
    bw.oracle()


def test_overlapping_batches_and_single_corrections_at_most_one_wins(bw):
    def op(i):
        if i % 3 == 0:
            return bw.ada.batch([it(bw.p[0], 1), it(bw.p[2], 1)])
        if i % 3 == 1:
            return bw.ada.batch([it(bw.p[2], 2), it(bw.p[1], 2)])
        return bw.cy.correct(bw.p[2]["payment_id"], amount=3, effective_at=bw.p[2]["created_at"])
    res = m.burst(op, 30)
    m.assert_no_5xx(res)
    assert len(bw.cy.revisions(bw.p[2]["payment_id"])) == 2, "the contested payment got exactly one new revision"
    assert [r.status_code for r in res].count(201) == 1, m.codes(res)
    bw.oracle()


def test_concurrent_identical_batch_applies_once(bw):
    k = new_key()
    items = [it(bw.p[0], 5), it(bw.p[1], 5)]
    res = m.burst(lambda i: bw.ada.batch(items, key=k), 30)
    m.assert_no_5xx(res)
    assert sorted(m.tally(res).items()) == [(200, 29), (201, 1)]
    assert len({r.text for r in res}) == 1
    assert len(bw.ada.revisions(bw.p[0]["payment_id"])) == 2
    bw.oracle()


def test_mixed_writers_with_batches_conserve_money(bw):
    def op(i):
        k = i % 5
        if k == 0:
            return bw.ada.batch([it(bw.p[i % 3], (i * 7) % 50, rev=1 + (i % 2))])
        if k == 1:
            return bw.bob.pay("cy", 1 + i % 9)
        if k == 2:
            return bw.bob.refund(bw.p[0]["payment_id"], 5)
        if k == 3:
            return bw.ada.batch(members(bw, (i % 40, i % 20)))
        return bw.dee.get("/statement", params={"limit": 4})
    res = m.burst(op, 150)
    m.assert_no_5xx(res)
    for r in res:
        assert r.status_code in (200, 201, 403, 404, 409, 422), (r.status_code, r.text[:200])
    assert sum(bw.balances().values()) == bw.total
    bw.oracle()
