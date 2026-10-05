"""Robustness of the stage-2 endpoints: malformed, oversized, wrongly typed and missing
input, odd methods and paths. Always the specified error, never a 5xx or a timeout."""
from __future__ import annotations

import pytest

import pf_model as m
from pf_client import expect, expect_error, expect_one_of, new_key


@pytest.fixture
def rw(make_world):
    w = make_world()
    w.aid = expect(w.ada.authorize("bob", 1_000), 201).json()["authorization_id"]
    return w


def test_invalid_utf8_bodies_are_malformed(rw):
    raw = b'{"to_handle":"bob","amount":1,"note":"\xff\xfe"}'
    expect_error(rw.ada.post("/authorizations", content=raw, key=new_key()), 400,
                 "malformed_request")
    expect_error(rw.bob.post(f"/authorizations/{rw.aid}/capture", content=b'{"amount":\xff}',
                             key=new_key()), 400, "malformed_request")
    rw.oracle()


def test_oversized_note_is_validation_not_a_crash(rw):
    expect_error(rw.ada.authorize("bob", 1, note="x" * 1_000_000), 422, "validation_failed")
    rw.oracle()


def test_deeply_nested_unknown_field_never_5xx(rw):
    raw = '{"amount":5,"zzz":' + "[" * 5000 + "]" * 5000 + "}"
    resp = rw.bob.post(f"/authorizations/{rw.aid}/capture", content=raw, key=new_key())
    assert resp.status_code in (201, 400), resp.status_code
    rw.oracle()


@pytest.mark.parametrize("raw", ['{"amount": NaN}', '{"amount": Infinity}',
                                 '{"amount": -Infinity}', '{"amount": 01}',
                                 '{"amount": 5,}', "{'amount': 5}"])
def test_non_json_number_tokens_are_malformed(rw, raw):
    expect_error(rw.bob.post(f"/authorizations/{rw.aid}/capture", content=raw,
                             key=new_key()), 400, "malformed_request")


@pytest.mark.parametrize("raw,status", [('{"amount": 1e400}', 422), ('{"amount": 1e-400}', 422),
                                        ('{"amount": 5e-1}', 422),
                                        ('{"amount": 1.0000000000000001e2}', 422)])
def test_extreme_numeric_literals(rw, raw, status):
    resp = rw.bob.post(f"/authorizations/{rw.aid}/capture", content=raw, key=new_key())
    expect_one_of(resp, (422, "validation_failed"))
    rw.oracle()


@pytest.mark.parametrize("method,path", [
    ("GET", "/authorizations/{aid}/capture"), ("GET", "/authorizations/{aid}/void"),
    ("PUT", "/authorizations"), ("DELETE", "/authorizations/{aid}"),
    ("GET", "/authorizations/{aid}"), ("PATCH", "/authorizations/{aid}/capture"),
    ("POST", "/authorizations/{aid}/release"), ("POST", "/authorizations/{aid}/capture/x"),
    ("POST", "/authorizations//capture"), ("POST", "/authorization"),
])
def test_unknown_routes_and_methods_are_404(rw, method, path):
    resp = rw.bob.request(method, path.format(aid=rw.aid), json={}, key=new_key())
    expect_error(resp, 404, "not_found")
    rw.oracle()


def test_very_long_and_odd_ids_in_the_path(rw):
    for aid in ("a" * 5_000, "%00", "..", "a_1%2Fcapture", "é", "' OR 1=1 --"):
        resp = rw.bob.post(f"/authorizations/{aid}/capture", json={}, key=new_key())
        assert resp.status_code in (404,), (aid, resp.status_code)
        resp = rw.ada.post(f"/authorizations/{aid}/void", json={})
        assert resp.status_code in (404,), (aid, resp.status_code)
    rw.oracle()


def test_garbage_burst_on_new_endpoints_never_5xx(rw):
    clients = [rw.new_client(h) for h in ("ada", "bob", "cy") for _ in range(4)]
    bodies = ["", "{", "[]", "null", '{"amount": "x"}', '{"amount": -1}', '{"final": "x"}',
              '{"to_handle": 5}', '{"to_handle": "bob", "amount": 1e309}', "\x00" * 100]
    paths = ["/authorizations", f"/authorizations/{rw.aid}/capture",
             f"/authorizations/{rw.aid}/void", "/authorizations/zz/capture"]

    def go(i):
        c = clients[i % len(clients)]
        return c.post(paths[i % len(paths)], content=bodies[i % len(bodies)],
                      key=new_key() if i % 3 else None)

    out = m.burst(go, 50)
    m.assert_no_5xx(out)
    for r in out:
        assert r.status_code in (200, 201, 400, 403, 404, 409, 422), r.status_code
    rw.oracle()


@pytest.mark.parametrize("hdr", ["Bearer", "Bearer ", "Basic abc", "bearer  x", "x"])
def test_bad_authorization_headers_on_new_endpoints(rw, hdr):
    for method, path in (("GET", "/authorizations"), ("POST", "/authorizations"),
                         ("POST", f"/authorizations/{rw.aid}/void")):
        resp = rw.ada.request(method, path, json={}, token=None, key=new_key(),
                              headers={"Authorization": hdr})
        expect_error(resp, 401, "unauthenticated")


def test_query_string_on_writes_is_ignored(rw):
    resp = rw.bob.post(f"/authorizations/{rw.aid}/capture", json={"amount": 5},
                       params={"amount": "999", "final": "false"}, key=new_key())
    assert expect(resp, 201).json()["amount"] == 5
    assert rw.bob.auth(rw.aid)["status"] == "captured"
