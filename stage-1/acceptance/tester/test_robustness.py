"""§5 robustness: malformed, oversized, wrongly typed and missing input. Never 5xx."""
from __future__ import annotations

import json

import pytest

import pf_model as m
from pf_client import expect, expect_error, new_key

WRITES = ["/payments", "/requests", "/splits", "/settlements", "/requests/RID/pay"]


@pytest.fixture
def rw(make_world):
    w = make_world(m.fixture(operators=["u_ada"]))
    w.rid = expect(w.bob.ask("ada", 10), 201).json()["request_id"]
    return w


def _path(w, p):
    return p.replace("RID", w.rid)


GARBAGE = ["", " ", "{", "}", "{\"a\":}", "{'a': 1}", "{\"a\":1,}", "nul", "[", "\x00",
           "{\"a\":1}{\"b\":2}", "\ufeff{}"]


@pytest.mark.parametrize("path", WRITES)
@pytest.mark.parametrize("raw", GARBAGE, ids=range(len(GARBAGE)))
def test_garbage_bodies_are_malformed(rw, path, raw):
    """[§5] unparseable -> 400 malformed_request, nothing changes."""
    before = rw.balances()
    resp = rw.ada.post(_path(rw, path), content=raw.encode("utf-8"), key=new_key())
    if raw == "\ufeff{}":
        assert resp.status_code in (201, 400, 422), resp.status_code  # BOM: never a crash
    else:  # D16: an empty body on a body-taking endpoint is unparseable too
        expect_error(resp, 400, "malformed_request")
    assert rw.balances() == before


@pytest.mark.parametrize("path", WRITES)
@pytest.mark.parametrize("raw", ["[]", "[1]", "\"s\"", "7", "true", "null"])
def test_non_object_bodies_are_malformed(rw, path, raw):
    expect_error(rw.ada.post(_path(rw, path), content=raw, key=new_key()),
                 400, "malformed_request")


@pytest.mark.parametrize("path", WRITES)
def test_oversized_body_never_5xx(rw, path):
    """[§5] a 2 MB note is a rule violation, not a crash or a timeout."""
    body = {"to_handle": "bob", "payer_handle": "bob", "amount": 1, "note": "x" * 2_000_000,
            "participant_handles": ["bob"],
            "transfers": [{"from_handle": "bob", "to_handle": "cy", "amount": 1,
                           "note": "x" * 2_000_000}]}
    resp = rw.ada.post(_path(rw, path), json=body, key=new_key(), timeout=10)
    if path.endswith("/pay"):
        expect(resp, 201)  # pay's only field is visibility; the rest is ignored
    else:
        expect_error(resp, 422, "validation_failed")  # D15: 2 MB is under the 64 MiB cap


def test_deeply_nested_body_never_5xx(rw):
    raw = "{\"to_handle\":\"bob\",\"amount\":1,\"zzz\":" + "[" * 5000 + "]" * 5000 + "}"
    resp = rw.ada.post("/payments", content=raw, key=new_key())
    assert resp.status_code in (201, 400, 413, 422), resp.status_code


def test_huge_array_of_participants(rw):
    handles = [f"h{i}" for i in range(20_000)]
    resp = rw.ada.split(10, handles)
    assert resp.status_code in (404, 413, 422), resp.status_code


def test_very_long_key_and_path_never_5xx(rw):
    expect_error(rw.ada.pay("bob", 1, key="k" * 5000), 422, "validation_failed")
    resp = rw.ada.pay_request("r" * 3000)
    assert resp.status_code in (404, 414, 431), resp.status_code


def test_unicode_key_is_fine(rw):
    key = "\u00e9\U0001F600-key"
    first = expect(rw.ada.post("/payments", json={"to_handle": "bob", "amount": 1},
                               headers={"Idempotency-Key": key.encode("utf-8")}),
                   201).json()
    again = rw.ada.post("/payments", json={"to_handle": "bob", "amount": 1},
                        headers={"Idempotency-Key": key.encode("utf-8")})
    assert expect(again, 200).json() == first


@pytest.mark.parametrize("ctype", ["text/plain", "application/x-www-form-urlencoded", None])
def test_content_type_is_not_enforced(rw, ctype):
    """[D16] Content-Type not enforced: a JSON body is a JSON body."""
    hdrs = {"Content-Type": ctype} if ctype else {}
    resp = rw.ada._client.post("/payments", content=json.dumps({"to_handle": "bob", "amount": 1}),
                               headers={**hdrs, "Authorization": f"Bearer {rw.ada.token}",
                                        "Idempotency-Key": new_key()})
    expect(resp, 201)


@pytest.mark.parametrize("path", ["/me", "/activity", "/requests", "/health"])
def test_get_with_a_body_never_5xx(rw, path):
    assert rw.ada.get(path, content="{garbage").status_code < 500


@pytest.mark.parametrize("params", [{"limit": "1" * 400}, {"offset": "9" * 400},
                                    {"limit": "-0"}, {"offset": "0" * 50 + "1"}])
def test_extreme_query_values(rw, params):
    resp = rw.ada.get("/activity", params=params)
    if params.get("offset", "").startswith("0" * 50):
        expect(resp, 200)
    else:
        expect_error(resp, 422, "validation_failed")


def test_repeated_query_parameter_first_wins(rw):
    """[D20] first occurrence wins."""
    for _ in range(8):
        expect(rw.ada.pay("bob", 1), 201)
    assert len(expect(rw.ada.get("/activity?limit=5&limit=6"), 200).json()["payments"]) == 5
    expect_error(rw.ada.get("/activity?limit=x&limit=6"), 422, "validation_failed")


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
def test_unsupported_methods_are_404(rw, method):
    """[D21] unknown route or method -> 404 not_found with the envelope."""
    for path in ("/payments", "/me", "/requests/RID/pay"):
        expect_error(rw.ada.request(method, _path(rw, path), json={}), 404, "not_found")


def test_unknown_route_is_404_before_anything_else(rw, api):
    """[D1 step 1, D21] route match comes first: garbage body, no token -> still 404."""
    expect_error(rw.ada.get("/definitely/not/here"), 404, "not_found")
    expect_error(api().post("/nope", content="{", token=None), 404, "not_found")
    expect_error(rw.ada.get("/me/extra"), 404, "not_found")


@pytest.mark.parametrize("name", ["/auth/signup", "/auth/login"])
def test_auth_oversized_inputs_never_5xx(rw, api, name):
    body = {"email": "a" * 100_000 + "@example.com", "password": "p" * 100_000,
            "display_name": "d" * 100_000}
    resp = api().post(name, json=body, token=None, timeout=10)
    assert resp.status_code < 500 and resp.status_code != 0


def test_reset_with_garbage_is_4xx(control):
    resp = control.post("/_test/reset", content="{", headers={"Content-Type": "application/json"})
    expect_error(resp, 400, "malformed_request")


def test_reset_with_invalid_fixture_changes_nothing(rw, control):
    before = rw.balances()
    """[R4, D18] incoherent fixtures -> 422 validation_failed, previous state still serves."""
    dup_handle = m.fixture([m.user("ada", 1), m.user("ada", 2, uid="u_x", email="x@example.com")])
    dup_id = m.fixture([m.user("ada", 1), m.user("bob", 2, uid="u_ada")])
    dup_email = m.fixture([m.user("ada", 1), m.user("bob", 2, email="ADA@example.com")])
    bad_handle = m.fixture([m.user("Ada", 1)])
    bad_pay = m.fixture(payments=[{"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_zz",
                                   "amount": 5, "note": "", "visibility": "public"}])
    bad_req = m.fixture(requests=[{"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada",
                                   "amount": 5, "note": "", "status": "weird"}])
    bad_op = m.fixture(operators=["u_nobody"])
    frac = m.fixture([m.user("ada", 1.5)])
    for bad in ({"currency": "EUR"}, {"users": "x", "currency": "EUR", "minor_units": 2},
                {**m.fixture(), "minor_units": 7}, dup_handle, dup_id, dup_email, bad_handle,
                bad_pay, bad_req, bad_op, frac, [], "x"):
        expect_error(control.post("/_test/reset", json=bad), 422, "validation_failed")
    assert rw.balances() == before
    expect(rw.ada.get("/me"), 200)


def test_service_still_healthy_after_abuse(rw, control):
    expect(control.get("/health"), 200)
    rw.oracle()


def test_missing_key_precedes_unknown_path_resource(rw):
    """[D1] key check (step 5) precedes the path resource lookup (step 8)."""
    expect_error(rw.ada.post("/requests/rq_nope/pay", json={}), 400, "missing_idempotency_key")
    expect_error(rw.ada.post("/requests/rq_nope/pay", json={}, key=new_key()), 404, "not_found")


def test_fixture_api_rules_not_applied_to_seed(make_world):
    """[D18] short seeded passwords and odd emails are fine in a fixture."""
    w = make_world(m.fixture([m.user("ada", 5, password="x"), m.user("bob", 0)]))
    assert w.ada.balance() == 5
