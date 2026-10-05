"""Export/import across v1..v4 state; stage-5 surface absent."""
from __future__ import annotations

import json
import os

import httpx
import pytest

import pf_model as m
from pf_client import Api, expect, expect_error, new_key

FAR = "2999-01-01T00:00:00+00:00"


def _post(c, path, body):
    return c.post(path, content=json.dumps(body), headers={"Content-Type": "application/json"})


def _export(c):
    return expect(c.get("/_test/export"), 200).json()


def _import(c, snap):
    expect(_post(c, "/_test/import", snap), 204)


@pytest.fixture
def cur(base_url):
    c = httpx.Client(base_url=base_url, timeout=10.0)
    yield c
    c.close()


@pytest.fixture
def ow(op_world):
    w = op_world
    w.p = [expect(w.ada.pay("bob", 100), 201).json(), expect(w.bob.pay("cy", 200), 201).json()]
    w.s = expect(w.ada.settle([{"from_handle": "ada", "to_handle": "cy", "amount": 40},
                               {"from_handle": "bob", "to_handle": "dee", "amount": 20}]), 201).json()
    return w


def test_v4_round_trip_keeps_refunds_batches_and_idempotency(ow, cur, reset, base_url):
    w = ow
    kr, kb = new_key(), new_key()
    refund = expect(w.bob.refund(w.p[0]["payment_id"], 40, key=kr), 201).json()
    items = [m.item(w.s["payments"][0]["payment_id"], 10, at_=w.s["committed_at"]),
             m.item(w.s["payments"][1]["payment_id"], 5, at_=w.s["committed_at"])]
    batch = expect(w.ada.batch(items, key=kb), 201).json()
    expect(w.ada.correct(w.p[0]["payment_id"], expected_revision=1, amount=60,
                         effective_at=w.p[0]["created_at"]), 201)
    first = w.ada.statement(limit=1)
    snap = _export(cur)
    assert snap["state"].get("schema_version") == 4
    want = {h: c.statement_all() for h, c in w.clients.items()}
    me = {h: c.me() for h, c in w.clients.items()}
    tokens = {h: c.token for h, c in w.clients.items()}
    reset(m.fixture([m.user("zed", 1)]))
    _import(cur, snap)
    cl = {h: Api(base_url, token=t) for h, t in tokens.items()}
    assert {h: c.me() for h, c in cl.items()} == me
    for h, c in cl.items():
        got = c.statement_all()
        got.pop("snapshot"), want[h].pop("snapshot")
        assert got == want[h]
    assert expect(cl["bob"].refund(w.p[0]["payment_id"], 40, key=kr), 200).json() == refund
    assert expect(cl["ada"].batch(items, key=kb), 200).json() == batch
    revs = cl["ada"].revisions(w.s["payments"][0]["payment_id"])
    assert revs[1]["correction_batch_id"] == batch["correction_batch_id"]
    feed = {p["payment_id"]: p for p in cl["ada"].feed()}
    assert feed[refund["payment_id"]]["refund_of"] == w.p[0]["payment_id"]
    # counters continue: new ids do not collide
    r2 = expect(cl["bob"].refund(w.p[0]["payment_id"], 10), 201).json()
    assert r2["payment_id"] != refund["payment_id"]
    b2 = expect(cl["ada"].batch([m.item(w.p[1]["payment_id"], 150, at_=w.p[1]["created_at"])]), 201).json()
    assert b2["correction_batch_id"] != batch["correction_batch_id"]
    expect_error(cl["bob"].refund(w.p[0]["payment_id"], 51), 422, "refund_exceeds_payment")
    ids = [p["payment_id"] for p in cl["ada"].feed()]
    assert len(ids) == len(set(ids))
    # the pre-export snapshot of the earlier stage is not required to survive; stability otherwise
    assert first["snapshot"]


def test_import_twice_is_idempotent(ow, cur):
    expect(ow.bob.refund(ow.p[0]["payment_id"], 40), 201)
    snap = _export(cur)
    _import(cur, snap)
    _import(cur, snap)
    assert len([p for p in ow.ada.feed() if p["refund_of"]]) == 1
    ow.oracle()


def test_unknown_future_schema_is_refused(ow, cur):
    snap = _export(cur)
    snap["state"]["schema_version"] = 99
    expect_error(_post(cur, "/_test/import", snap), 422, "validation_failed")
    assert ow.ada.balance() > 0


PREV = [("stage3", "PREVIOUS_BASE_URL"), ("stage2", "S2_BASE_URL"), ("stage1", "S1_BASE_URL")]


@pytest.fixture(params=PREV, ids=[p[0] for p in PREV])
def prev(request):
    url = os.environ.get(request.param[1])
    if not url:
        pytest.skip(f"{request.param[1]} not set")
    c = httpx.Client(base_url=url.rstrip("/"), timeout=10.0)
    c.base = url.rstrip("/")
    yield c
    c.close()


def test_older_exports_keep_settlements_corrections_and_gain_refunds(prev, cur, reset, base_url):
    reset(m.fixture([m.user("zed", 1)]))
    fx = m.fixture([m.ADA, m.BOB, m.CY, m.user("dee", 0)], operators=["u_ada"])
    expect(_post(prev, "/_test/reset", fx), 204)
    old = {h: Api(prev.base).authenticate(f"{h}@example.com") for h in ("ada", "bob", "cy")}
    p1 = expect(old["ada"].pay("bob", 500), 201).json()
    s = expect(old["ada"].settle([{"from_handle": "ada", "to_handle": "cy", "amount": 40},
                                  {"from_handle": "bob", "to_handle": "dee", "amount": 20}]), 201).json()
    corr = None
    if prev.base == os.environ.get("PREVIOUS_BASE_URL"):                  # stage 3 has corrections
        corr = expect(old["ada"].correct(p1["payment_id"], amount=300, effective_at=p1["created_at"]), 201).json()
    bal = {h: c.balance() for h, c in old.items()}
    snap = _export(prev)
    _import(cur, snap)
    new = {h: Api(base_url, token=c.token) for h, c in old.items()}
    assert {h: c.balance() for h, c in new.items()} == bal
    pay = [x for x in new["ada"].feed() if x["payment_id"] == p1["payment_id"]][0]
    assert pay["refund_of"] is None
    revs = new["ada"].revisions(p1["payment_id"])
    assert len(revs) == (2 if corr else 1)
    members = [x for x in new["cy"].feed() if x["settlement_id"] == s["settlement_id"]]
    assert members and all(x["refund_of"] is None for x in members)
    cur_amount = 300 if corr else 500
    r = expect(new["bob"].refund(p1["payment_id"], cur_amount), 201).json()
    assert r["refund_of"] == p1["payment_id"]
    expect_error(new["bob"].refund(p1["payment_id"], 1), 422, "refund_exceeds_payment")
    # settlement members can be corrected as a complete batch after import
    ops = Api(base_url).authenticate("ada@example.com")
    b = expect(ops.batch([m.item(x["payment_id"], 1, at_=s["committed_at"]) for x in s["payments"]]), 201).json()
    assert len(b["revisions"]) == 2
    tot = sum(Api(base_url).authenticate(f"{h}@example.com").balance() for h in ("ada", "bob", "cy", "dee"))
    assert tot == 10_000 + 2_500 + 500


LATER = [("GET", "/refunds"), ("POST", "/refunds"), ("GET", "/history"), ("GET", "/disputes"),
         ("POST", "/payments/{pid}/disputes"), ("GET", "/correction-batches/cb_1"),
         ("GET", "/correction-batches"), ("POST", "/payments/{pid}/chargebacks")]


@pytest.mark.parametrize("method,path", LATER, ids=[f"{a} {b}" for a, b in LATER])
def test_unspecified_surface_does_not_exist(ow, method, path):
    pid = ow.p[0]["payment_id"]
    kw = {"json": {"amount": 1}} if method == "POST" else {}
    resp = ow.ada.request(method, path.format(pid=pid), key=new_key(), **kw)
    expect_error(resp, 404, "not_found")


def test_no_later_fields(ow):
    later = {"dispute_id", "chargeback_of", "disputed", "closed_batch_at"}
    r = expect(ow.bob.refund(ow.p[0]["payment_id"], 5), 201).json()
    b = expect(ow.ada.batch([m.item(ow.p[1]["payment_id"], 100, at_=ow.p[1]["created_at"])]), 201).json()
    for body in (r, b, *b["revisions"], ow.ada.me(), ow.ada.statement()):
        assert not later & set(body), (later & set(body), body)
