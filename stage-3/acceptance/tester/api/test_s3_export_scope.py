"""Export/import across v1, v2 and v3 state, and the absence of stage-4 surface."""
from __future__ import annotations

import json
import os

import httpx
import pytest

import pf_model as m
from pf_client import Api, expect, expect_error, new_key
from hist import at

FAR = "2999-01-01T00:00:00+00:00"
LATER_STAGE = int(os.environ.get("PF_STAGE", "3")) >= 4     # DECISION PF_STAGE: stage-4 build


def _post(client, path, body):
    return client.post(path, content=json.dumps(body), headers={"Content-Type": "application/json"})


def _export(c):
    return expect(c.get("/_test/export"), 200).json()


def _import(c, snap):
    expect(_post(c, "/_test/import", snap), 204)


@pytest.fixture
def cur(base_url):
    c = httpx.Client(base_url=base_url, timeout=10.0)
    yield c
    c.close()


# ---- v3 round trip ----------------------------------------------------------------------------

def test_v3_export_round_trips_revisions_holds_and_counters(hw, cur, reset):
    expect(hw.ada.correct("p_1", amount=300, effective_at=at(4), reason="a"), 201)
    expect(hw.ada.correct("p_1", expected_revision=2, amount=450, effective_at=at(5), reason="b"), 201)
    api_pay = expect(hw.ada.pay("bob", 77, key="kk-1"), 201).json()
    a = expect(hw.ada.authorize("cy", 600), 201).json()
    expect(hw.cy.capture(a["authorization_id"], {"amount": 100, "final": False}), 201)
    b = expect(hw.ada.authorize("cy", 50), 201).json()
    expect(hw.ada.void(b["authorization_id"]), 200)
    snap = _export(cur)
    assert snap["track"] == "pocketful" and snap["format_version"] == 1
    want = {
        "bal": hw.balances(), "me": {h: c.me() for h, c in hw.clients.items()},
        "rev": hw.ada.revisions("p_1"), "auths": hw.ada.auths(),
        "st": {h: c.statement_all() for h, c in hw.clients.items()},
        "at": {h: c.me_at(as_of=at(4), known_at=FAR) for h, c in hw.clients.items()},
    }
    tokens = {h: c.token for h, c in hw.clients.items()}
    reset(m.fixture([m.user("zed", 1)]))                           # wipe, then restore
    _import(cur, snap)
    ada = Api(hw.ada.base_url, token=tokens["ada"])
    assert ada.revisions("p_1") == want["rev"]
    assert ada.auths() == want["auths"]
    assert {h: Api(ada.base_url, token=t).me() for h, t in tokens.items()} == want["me"]
    for h, t in tokens.items():
        c = Api(ada.base_url, token=t)
        st = c.statement_all()
        st.pop("snapshot")
        exp = dict(want["st"][h])
        exp.pop("snapshot")
        assert st == exp
        assert c.me_at(as_of=at(4), known_at=FAR) == want["at"][h]
    assert expect(ada.post("/payments", json={"to_handle": "bob", "amount": 77}, key="kk-1"), 200).json() == api_pay
    before = {p["payment_id"] for p in ada.feed()}
    nxt = expect(ada.pay("bob", 1), 201).json()
    assert nxt["payment_id"] not in before
    ids = [p["payment_id"] for p in ada.feed()]
    assert len(ids) == len(set(ids))
    expect(ada.correct(api_pay["payment_id"], amount=1, effective_at=api_pay["created_at"]), 201)


def test_import_twice_does_not_duplicate_revisions(hw, cur):
    expect(hw.ada.correct("p_1", amount=300, effective_at=at(4)), 201)
    snap = _export(cur)
    _import(cur, snap)
    _import(cur, snap)
    assert len(hw.ada.revisions("p_1")) == 2


def test_stage3_state_is_what_import_accepts_unchanged(hw, cur):
    snap = _export(cur)
    assert snap["state"].get("schema_version") == (4 if LATER_STAGE else 3)
    _import(cur, snap)
    snap["state"]["schema_version"] = 99
    expect_error(_post(cur, "/_test/import", snap), 422, "validation_failed")
    assert hw.ada.balance() == 10_000


# ---- v1 / v2 -> v3 ----------------------------------------------------------------------------

PREV = [("stage2", "PREVIOUS_BASE_URL"), ("stage1", "S1_BASE_URL")]


@pytest.fixture(params=PREV, ids=[p[0] for p in PREV])
def prev(request):
    url = os.environ.get(request.param[1])
    if not url:
        pytest.skip(f"{request.param[1]} not set")
    c = httpx.Client(base_url=url.rstrip("/"), timeout=10.0)
    c.base = url.rstrip("/")
    yield c
    c.close()


def test_older_exports_import_and_gain_history(prev, cur, reset, api, base_url):
    reset(m.fixture([m.user("zed", 1)]))
    fx = m.fixture([m.ADA, m.BOB, m.CY, m.user("dee", 0)], operators=["u_ada"])
    expect(_post(prev, "/_test/reset", fx), 204)
    old = {h: Api(prev.base).authenticate(f"{h}@example.com") for h in ("ada", "bob", "cy")}
    k = new_key()
    body = {"to_handle": "bob", "amount": 1_500, "note": "dinner", "visibility": "private"}
    p1 = expect(old["ada"].post("/payments", json=body, key=k), 201).json()
    p2 = expect(old["bob"].pay("cy", 300), 201).json()
    expect(old["ada"].settle([{"from_handle": "cy", "to_handle": "dee", "amount": 10}]), 201)
    r = expect(old["bob"].ask("ada", 50), 201).json()
    bal = {h: c.balance() for h, c in old.items()}
    snap = _export(prev)
    assert snap["format_version"] == 1
    _import(cur, snap)
    new = {h: Api(base_url, token=c.token) for h, c in old.items()}
    assert {h: c.balance() for h, c in new.items()} == bal
    assert expect(new["ada"].post("/payments", json=body, key=k), 200).json()["payment_id"] == p1["payment_id"]
    revs = new["ada"].revisions(p1["payment_id"])
    assert len(revs) == 1 and revs[0]["revision"] == 1 and revs[0]["reason"] == "" and revs[0]["amount"] == 1_500
    assert m.parse(revs[0]["effective_at"]) == m.parse(revs[0]["recorded_at"]) == m.parse(p1["created_at"])
    # opening balances are derived: ending balance minus every payment's net effect
    far = "1999-01-01T00:00:00+00:00"
    assert new["ada"].me_at(as_of=far)["balance"] == 10_000
    assert new["bob"].me_at(as_of=far)["balance"] == 2_500
    assert new["cy"].me_at(as_of=far)["balance"] == 500
    assert new["ada"].me_at(as_of=FAR)["balance"] == bal["ada"]
    st = new["bob"].statement_all()
    assert st["opening_balance"] == 2_500 and st["closing_balance"] == bal["bob"]
    got = [e["payment"]["payment_id"] for e in st["entries"]]
    assert got.index(p1["payment_id"]) < got.index(p2["payment_id"])
    expect(new["ada"].correct(p1["payment_id"], amount=1_000, effective_at=p1["created_at"]), 201)
    assert new["ada"].balance() == bal["ada"] + 500
    members = [e for e in new["cy"].statement_all()["entries"] if e["payment"]["settlement_id"]]
    assert members
    expect_error(new["cy"].correct(members[0]["payment"]["payment_id"], amount=1), 422,
                 "linked_payment_immutable")
    assert [x for x in new["ada"].requests_list() if x["request_id"] == r["request_id"]][0]["status"] == "pending"
    expect(new["ada"].pay_request(r["request_id"]), 201)
    dee = Api(base_url).authenticate("dee@example.com")
    assert sum(c.me()["balance"] for c in (*new.values(), dee)) == 13_000


def test_stage2_authorizations_survive_into_stage3(cur, reset, api, base_url):
    url = os.environ.get("PREVIOUS_BASE_URL")
    if not url:
        pytest.skip("PREVIOUS_BASE_URL not set")
    prev = httpx.Client(base_url=url.rstrip("/"), timeout=10.0)
    try:
        expect(_post(prev, "/_test/reset", m.fixture()), 204)
        ada = Api(url).authenticate("ada@example.com")
        bob = Api(url).authenticate("bob@example.com")
        a = expect(ada.authorize("bob", 900), 201).json()
        c = expect(bob.capture(a["authorization_id"], {"amount": 200, "final": False}), 201).json()
        snap = _export(prev)
    finally:
        prev.close()
    _import(cur, snap)
    ada3 = Api(base_url, token=ada.token)
    bob3 = Api(base_url, token=bob.token)
    me = ada3.me()
    assert (me["total"], me["held"], me["available"]) == (9_800, 700, 9_100)
    got = ada3.auth(a["authorization_id"])
    assert got["status"] == "open" and got["remaining_amount"] == 700 and got["payment_ids"] == [c["payment_id"]]
    assert got["closed_at"] is None
    expect_error(ada3.correct(c["payment_id"], amount=1, effective_at=c["created_at"]), 422,
                 "linked_payment_immutable")
    expect(bob3.capture(a["authorization_id"]), 201)
    assert ada3.auth(a["authorization_id"])["closed_at"] is not None
    assert ada3.statement()["closing_balance"] == 9_100
    assert sum(cl.me()["total"] for cl in (ada3, bob3)) == 12_500


# ---- stage 4 must be absent -------------------------------------------------------------------

LATER = [("POST", "/payments/{pid}/refunds"), ("GET", "/payments/{pid}/refunds"),
         ("POST", "/correction-batches"), ("GET", "/correction-batches"),
         ("GET", "/correction-batches/cb_1"), ("GET", "/history"), ("GET", "/refunds")]


@pytest.mark.skipif(LATER_STAGE, reason="stage-3 overshoot guard; PF_STAGE>=4")
@pytest.mark.parametrize("method,path", LATER, ids=[f"{a} {b}" for a, b in LATER])
def test_stage4_endpoints_do_not_exist(op_world, method, path):
    pid = expect(op_world.ada.pay("bob", 5), 201).json()["payment_id"]
    kw = {"json": {"amount": 1, "reason": "x", "items": []}} if method == "POST" else {}
    resp = op_world.ada.request(method, path.format(pid=pid), key=new_key(), **kw)
    expect_error(resp, 404, "not_found")
    assert op_world.ada.balance() == 9_995


@pytest.mark.skipif(LATER_STAGE, reason="stage-3 overshoot guard; PF_STAGE>=4")
def test_no_stage4_fields_anywhere(hw):
    later = {"refund_of", "refunds", "correction_batch_id", "batch_id", "incomplete_settlement"}
    p = expect(hw.ada.pay("bob", 5), 201).json()
    r = expect(hw.ada.correct(p["payment_id"], amount=2, effective_at=p["created_at"]), 201).json()
    a = expect(hw.ada.authorize("bob", 5), 201).json()
    bodies = [p, r, a, hw.ada.me(), hw.ada.statement(), *hw.ada.revisions(p["payment_id"]),
              *hw.ada.feed(), hw.ada.statement()["entries"][0]["payment"]]
    for body in bodies:
        assert not later & set(body), (later & set(body), body)
