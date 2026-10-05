"""Black-box HTTP client for the pocketful stage-2 acceptance suite (tester seat).

Only the public HTTP interface is used. Every 4xx/5xx is checked for the §5 envelope
`{"error": {"code": ..., "message": ...}}`; the message wording is never asserted.
"""
from __future__ import annotations

import json as _json
import uuid
from typing import Any

import httpx

REQUEST_TIMEOUT = 5.0   # §2 per-request timeout
CONTROL_TIMEOUT = 10.0  # §2/§10 reset, export, import


def new_key() -> str:
    return uuid.uuid4().hex


class Api:
    """One client. `token=None` on a call sends no Authorization header."""

    def __init__(self, base_url: str, token: str | None = None,
                 timeout: float = REQUEST_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def request(self, method: str, path: str, *, json: Any = ..., content: Any = None,
                key: str | None = None, token: Any = ..., headers: dict | None = None,
                params: Any = None, timeout: float | None = None) -> httpx.Response:
        hdrs = dict(headers or {})
        tok = self.token if token is ... else token
        if tok is not None:
            hdrs.setdefault("Authorization", f"Bearer {tok}")
        if key is not None:
            hdrs.setdefault("Idempotency-Key", key)
        kw: dict = {"headers": hdrs}
        if content is not None:
            hdrs.setdefault("Content-Type", "application/json")
            kw["content"] = content.encode() if isinstance(content, str) else content
        elif json is not ...:
            hdrs.setdefault("Content-Type", "application/json")
            kw["content"] = _json.dumps(json).encode()
        if params is not None:
            kw["params"] = params
        if timeout is not None:
            kw["timeout"] = timeout
        return self._client.request(method, path, **kw)

    def get(self, path: str, **kw) -> httpx.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw) -> httpx.Response:
        return self.request("POST", path, **kw)

    # -- auth --------------------------------------------------------------
    def signup(self, email: str, password: str = "correct horse",
               display_name: str = "New") -> httpx.Response:
        return self.post("/auth/signup", json={"email": email, "password": password,
                                               "display_name": display_name}, token=None)

    def login(self, email: str, password: str = "correct horse") -> httpx.Response:
        return self.post("/auth/login", json={"email": email, "password": password},
                         token=None)

    def authenticate(self, email: str, password: str = "correct horse") -> "Api":
        resp = self.login(email, password)
        expect(resp, 200)
        self.token = resp.json()["token"]
        return self

    # -- wallet helpers ------------------------------------------------------
    def me(self) -> dict:
        return expect(self.get("/me"), 200).json()

    def balance(self) -> int:
        return self.me()["balance"]

    def pay(self, to: str, amount: Any, key: str | None = None, **extra) -> httpx.Response:
        body = {"to_handle": to, "amount": amount, **extra}
        return self.post("/payments", json=body, key=key or new_key())

    def ask(self, payer: str, amount: Any, key: str | None = None, **extra) -> httpx.Response:
        body = {"payer_handle": payer, "amount": amount, **extra}
        return self.post("/requests", json=body, key=key or new_key())

    def pay_request(self, rid: str, body: Any = None, key: str | None = None) -> httpx.Response:
        return self.post(f"/requests/{rid}/pay", json={} if body is None else body,
                         key=key or new_key())

    def split(self, amount: Any, handles: Any, key: str | None = None, **extra) -> httpx.Response:
        body = {"amount": amount, "participant_handles": handles, **extra}
        return self.post("/splits", json=body, key=key or new_key())

    def settle(self, transfers: Any, key: str | None = None) -> httpx.Response:
        return self.post("/settlements", json={"transfers": transfers}, key=key or new_key())

    def all_pages(self, path: str, field: str, **params) -> list[dict]:
        out, offset = [], 0
        while True:
            body = expect(self.get(path, params={**params, "limit": 200, "offset": offset}),
                          200).json()
            out.extend(body[field])
            if not body["has_more"]:
                return out
            offset += 200
            assert offset < 100_000, "runaway pagination"

    def feed(self) -> list[dict]:
        return self.all_pages("/activity", "payments")

    def requests_list(self, **params) -> list[dict]:
        return self.all_pages("/requests", "requests", **params)

    # -- stage 2: authorizations ----------------------------------------------
    def authorize(self, to: str, amount: Any, key: str | None = None,
                  **extra) -> httpx.Response:
        body = {"to_handle": to, "amount": amount, **extra}
        return self.post("/authorizations", json=body, key=key or new_key())

    def capture(self, aid: str, body: Any = None, key: str | None = None) -> httpx.Response:
        return self.post(f"/authorizations/{aid}/capture", json={} if body is None else body,
                         key=key or new_key())

    def void(self, aid: str) -> httpx.Response:
        return self.post(f"/authorizations/{aid}/void", json={})

    def auths(self, **params) -> list[dict]:
        return self.all_pages("/authorizations", "authorizations", **params)

    def auth(self, aid: str) -> dict:
        found = [a for a in self.auths() if a["authorization_id"] == aid]
        assert len(found) == 1, f"authorization {aid} listed {len(found)} times"
        return found[0]


# ---- assertions -------------------------------------------------------------

def describe(resp: httpx.Response) -> str:
    text = resp.text
    if len(text) > 500:
        text = text[:500] + "..."
    return f"{resp.request.method} {resp.request.url} -> {resp.status_code} {text!r}"


def expect(resp: httpx.Response, status: int) -> httpx.Response:
    assert resp.status_code == status, f"expected {status}. {describe(resp)}"
    return resp


def code_of(resp: httpx.Response) -> str | None:
    try:
        body = resp.json()
    except ValueError:
        raise AssertionError(f"error body is not JSON. {describe(resp)}") from None
    assert isinstance(body, dict) and isinstance(body.get("error"), dict), \
        f"error envelope must be {{'error': {{...}}}}. {describe(resp)}"
    err = body["error"]
    assert isinstance(err.get("code"), str) and err["code"], f"error.code missing. {describe(resp)}"
    assert isinstance(err.get("message"), str), f"error.message must be a string. {describe(resp)}"
    return err["code"]


def expect_error(resp: httpx.Response, status: int, code: str) -> httpx.Response:
    actual = code_of(resp) if resp.status_code >= 400 else None
    assert (resp.status_code, actual) == (status, code), \
        f"expected {status} {code}, got {resp.status_code} {actual}. {describe(resp)}"
    return resp


def expect_one_of(resp: httpx.Response, *pairs: tuple[int, str]) -> httpx.Response:
    actual = code_of(resp) if resp.status_code >= 400 else None
    assert (resp.status_code, actual) in pairs, \
        f"expected one of {pairs}, got {resp.status_code} {actual}. {describe(resp)}"
    return resp
