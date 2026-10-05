"""§6 authentication and §4 derived handles."""
from __future__ import annotations

import os

import pytest

import pf_model as m
from pf_client import Api, expect, expect_error, new_key

PF_STAGE = int(os.environ.get("PF_STAGE", "1"))


def test_signup_shape_and_new_wallet(world, api):
    """[§6, §4] 201 {user_id, display_name, token}; new users start at 0."""
    body = expect(api().signup("dee@example.com", "correct horse", "Dee D"), 201).json()
    assert set(body) >= {"user_id", "display_name", "token"}
    assert body["display_name"] == "Dee D" and body["token"]
    me = api(body["token"]).me()
    stage1 = {"user_id": body["user_id"], "display_name": "Dee D", "handle": "dee",
              "balance": 0, "currency": "EUR", "minor_units": 2}
    if PF_STAGE >= 2:   # DECISION PF_STAGE: stage 2 adds total/available/held to /me
        assert {k: me.get(k) for k in stage1} == stage1 and me["balance"] == me["total"] == 0
    else:
        assert me == stage1


def test_login_shape(world, api):
    """[§6]"""
    body = expect(api().login("bob@example.com"), 200).json()
    assert body["user_id"] == "u_bob" and body["display_name"] == "Bob" and body["token"]


def test_new_user_can_receive_and_be_asked_immediately(world, api):
    """[§4]"""
    dee = api(expect(api().signup("dee@example.com"), 201).json()["token"])
    expect(world.ada.pay("dee", 300), 201)
    rq = expect(world.bob.ask("dee", 99999), 201).json()
    assert rq["payer_handle"] == "dee" and rq["status"] == "pending"
    assert dee.balance() == 300
    expect(dee.pay("bob", 300), 201)
    assert dee.balance() == 0


@pytest.mark.parametrize("email,handle", [
    ("Dee.Ann+tag@example.com", "dee_ann_tag"),
    ("UPPER@example.com", "upper"),
    ("a-b.c@example.com", "a_b_c"),
    ("under_score9@example.com", "under_score9"),
    ("abcdefghijklmnopqrstuvwxyz@example.com", "abcdefghijklmnopqrst"),
    ("x" * 20 + "@example.com", "x" * 20),
    ("x" * 19 + ".y@example.com", "x" * 19 + "_"),
    ("jos\u00e9@example.com", "jos_"),
])
def test_handle_is_derived_from_the_email(world, api, email, handle):
    """[§4] lowercase, non [a-z0-9_] -> _, truncate to 20."""
    tok = expect(api().signup(email), 201).json()["token"]
    assert api(tok).me()["handle"] == handle


def test_derived_handle_is_payable(world, api):
    """[§4] users identify recipients by handle."""
    expect(api().signup("Mo.Ri@example.com"), 201)
    expect(world.ada.pay("mo_ri", 5), 201)


@pytest.mark.parametrize("email", ["ada@elsewhere.org", "ADA@elsewhere.org", "a.d@x.com"])
def test_handle_taken_creates_no_account(world, api, email):
    """[§6] derived handle already taken -> 409 handle_taken, no account created."""
    if email == "a.d@x.com":
        expect(api().signup("a_d@y.com"), 201)
    expect_error(api().signup(email), 409, "handle_taken")
    expect_error(api().login(email), 401, "unauthenticated")


def test_truncated_handle_collision_is_handle_taken(world, api):
    """[§4, §6] collision after truncation."""
    expect(api().signup("abcdefghijklmnopqrstUVW@example.com"), 201)
    expect_error(api().signup("abcdefghijklmnopqrstXYZ@example.com"), 409, "handle_taken")


def test_email_taken(world, api):
    """[§6] seeded and signed-up emails are both taken."""
    expect_error(api().signup("ada@example.com"), 409, "email_taken")
    expect(api().signup("dee@example.com"), 201)
    expect_error(api().signup("dee@example.com", "another pass", "Other"), 409, "email_taken")


@pytest.mark.parametrize("password,status", [("1234567", 422), ("12345678", 201),
                                             ("", 422), ("\u00e9" * 7, 422)])
def test_password_minimum_length(world, api, password, status):
    """[§6] shorter than 8 characters -> 422 validation_failed; exactly 8 is fine."""
    resp = api().signup("pw@example.com", password)
    if status == 201:
        expect(resp, 201)
        expect(api().login("pw@example.com", password), 200)
    else:
        expect_error(resp, 422, "validation_failed")
        expect_error(api().login("pw@example.com", password or "x"), 401, "unauthenticated")


@pytest.mark.parametrize("email", ["plainaddress", "@example.com", "dee@", "", "dee example.com",
                                   "a@b@example.com", "de e@example.com", "dee@exa mple.com"])
def test_bad_email_format(world, api, email):
    """[§6] not local@domain -> 422 validation_failed."""
    expect_error(api().signup(email), 422, "validation_failed")


@pytest.mark.parametrize("missing", ["email", "password", "display_name"])
def test_signup_missing_required_field(world, api, missing):
    """[§5] a required field missing -> 422."""
    body = {"email": "dee@example.com", "password": "correct horse", "display_name": "Dee"}
    del body[missing]
    expect_error(api().post("/auth/signup", json=body, token=None), 422, "validation_failed")


@pytest.mark.parametrize("field,value", [("email", 5), ("password", 12345678),
                                         ("email", ["dee@example.com"]),
                                         ("password", None), ("display_name", 5),
                                         ("display_name", None)])
def test_signup_wrong_type_is_malformed(world, api, field, value):
    """[§5] a field of the wrong JSON type -> 400 malformed_request."""
    body = {"email": "dee@example.com", "password": "correct horse", "display_name": "Dee"}
    body[field] = value
    expect_error(api().post("/auth/signup", json=body, token=None), 400, "malformed_request")
    expect_error(api().login("dee@example.com"), 401, "unauthenticated")


@pytest.mark.parametrize("path", ["/auth/signup", "/auth/login"])
@pytest.mark.parametrize("raw", ["{", "not json", "[]", "\"x\"", "42", "null"])
def test_auth_unparseable_or_non_object_body(world, api, path, raw):
    """[§5] unparseable body / non-object body -> 400 malformed_request."""
    expect_error(api().post(path, content=raw, token=None), 400, "malformed_request")


@pytest.mark.parametrize("email,password", [("ada@example.com", "wrong horse"),
                                            ("nobody@example.com", "correct horse"),
                                            ("ADA@EXAMPLE.COM", "Correct horse")])
def test_login_failures_are_401(world, api, email, password):
    """[§6] wrong password or unknown email -> 401 unauthenticated."""
    expect_error(api().login(email, password), 401, "unauthenticated")


def test_many_tokens_stay_valid_together(world, api):
    """[§6] multiple valid tokens, concurrent sessions, no expiry."""
    tokens = [expect(api().login("cy@example.com"), 200).json()["token"] for _ in range(3)]
    tokens.append(world.cy.token)
    assert len(set(tokens)) == len(tokens)
    for t in tokens:
        assert api(t).me()["handle"] == "cy"
    signup_tok = expect(api().signup("dee@example.com"), 201).json()["token"]
    login_tok = expect(api().login("dee@example.com"), 200).json()["token"]
    assert api(signup_tok).me()["handle"] == api(login_tok).me()["handle"] == "dee"


@pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer ", "Basic YWRhOnB3",
                                    "Bearer not-a-real-token", "Token abc"])
def test_bad_credentials_are_401(world, base_url, header):
    """[§6, §5] missing, malformed or unknown bearer token.

    Sent with http.client because httpx refuses some of these header values client-side.
    """
    import http.client
    import json as _json
    from urllib.parse import urlsplit
    u = urlsplit(base_url)
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=5)
    try:
        conn.putrequest("GET", "/me")
        if header is not None:
            conn.putheader("Authorization", header)
        conn.endheaders()
        resp = conn.getresponse()
        body = _json.loads(resp.read() or b"null")
    finally:
        conn.close()
    assert resp.status == 401 and body["error"]["code"] == "unauthenticated", (resp.status, body)


AUTHED = [("GET", "/me", None), ("GET", "/activity", None), ("GET", "/requests", None),
          ("POST", "/payments", {"to_handle": "bob", "amount": 1}),
          ("POST", "/requests", {"payer_handle": "bob", "amount": 1}),
          ("POST", "/requests/rq_x/pay", {}),
          ("POST", "/requests/rq_x/decline", {}),
          ("POST", "/requests/rq_x/cancel", {}),
          ("POST", "/splits", {"amount": 1, "participant_handles": ["bob"]}),
          ("POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob",
                                                   "amount": 1}]})]


@pytest.mark.parametrize("method,path,body", AUTHED, ids=[f"{a} {b}" for a, b, _ in AUTHED])
@pytest.mark.parametrize("with_key", [True, False])
def test_every_wallet_endpoint_requires_a_token(world, api, method, path, body, with_key):
    """[§6] no token -> 401 (also before a missing idempotency key, §5 order)."""
    kw = {} if body is None else {"json": body}
    resp = api().request(method, path, token=None, key=new_key() if with_key else None, **kw)
    expect_error(resp, 401, "unauthenticated")
    assert world.balances() == {"ada": 10_000, "bob": 2_500, "cy": 500}


def test_concurrent_signups_same_email_create_one_account(world, base_url):
    """[§6] adversarial: exactly one 201, the rest 409 email_taken."""
    clients = [Api(base_url) for _ in range(20)]
    try:
        out = m.burst(lambda i: clients[i].signup("race@example.com", f"password{i:03d}"), 20)
        m.assert_no_5xx(out)
        assert m.tally(out) == {201: 1, 409: 19}, m.tally(out)
        assert all(r.json()["error"]["code"] == "email_taken"
                   for r in out if r.status_code == 409)
        winner = next(i for i, r in enumerate(out) if r.status_code == 201)
        expect(clients[0].login("race@example.com", f"password{winner:03d}"), 200)
    finally:
        for c in clients:
            c.close()


def test_concurrent_signups_same_derived_handle(world, base_url):
    """[§4, §6] adversarial: different emails, one handle: one wins, rest handle_taken."""
    clients = [Api(base_url) for _ in range(20)]
    try:
        out = m.burst(lambda i: clients[i].signup(f"sam@host{i}.example"), 20)
        m.assert_no_5xx(out)
        assert m.tally(out) == {201: 1, 409: 19}, m.tally(out)
        assert {r.json()["error"]["code"] for r in out if r.status_code == 409} == {"handle_taken"}
        losers = [i for i, r in enumerate(out) if r.status_code == 409]
        expect_error(clients[0].login(f"sam@host{losers[0]}.example"), 401, "unauthenticated")
    finally:
        for c in clients:
            c.close()


def test_email_is_case_insensitive(world, api):
    """[D8] uniqueness and login compare emails case-insensitively."""
    expect_error(api().signup("ADA@Example.COM"), 409, "email_taken")
    body = expect(api().login("ADA@EXAMPLE.COM"), 200).json()
    assert body["user_id"] == "u_ada"
    expect(api().signup("Mixed.Case@Example.com"), 201)
    expect(api().login("mixed.case@example.com"), 200)
    expect_error(api().signup("MIXED.CASE@example.com"), 409, "email_taken")


def test_signup_precedence(world, api):
    """[D1 signup] types -> password -> email format -> display_name -> email_taken -> handle."""
    expect_error(api().post("/auth/signup", json={"email": 5, "password": "short"}, token=None),
                 400, "malformed_request")
    expect_error(api().signup("ada@example.com", "short"), 422, "validation_failed")
    expect_error(api().signup("not-an-email", "short"), 422, "validation_failed")
    expect_error(api().post("/auth/signup", json={"email": "ada@example.com",
                                                  "password": "correct horse"}, token=None),
                 422, "validation_failed")
    expect_error(api().signup("ada@example.com"), 409, "email_taken")


def test_bearer_scheme_is_case_insensitive(world, api):
    """[D17] scheme `Bearer` case-insensitive, one space, token."""
    tok = world.ada.token
    for scheme in ("bearer", "BEARER", "Bearer"):
        expect(api().get("/me", token=None, headers={"Authorization": f"{scheme} {tok}"}), 200)
    for bad in (f"Bearer  {tok}", f"Bearer {tok} extra", tok, f"Bearer{tok}"):
        expect_error(api().get("/me", token=None, headers={"Authorization": bad}),
                     401, "unauthenticated")
