"""Overshoot guard: the stage-1 folder must not expose later stages' surface."""
from __future__ import annotations

import pytest

from pf_client import expect, expect_error, new_key

LATER = [("GET", "/authorizations"), ("POST", "/authorizations"),
         ("POST", "/authorizations/a_1/capture"), ("POST", "/authorizations/a_1/void"),
         ("GET", "/statement"), ("GET", "/payments/p_1/revisions"),
         ("POST", "/payments/p_1/corrections"), ("POST", "/payments/p_1/refunds"),
         ("POST", "/correction-batches")]


@pytest.mark.parametrize("method,path", LATER, ids=[f"{a} {b}" for a, b in LATER])
def test_later_stage_endpoints_do_not_exist(world, method, path):
    kw = {"json": {"payee_handle": "bob", "amount": 1}} if method == "POST" else {}
    resp = world.ada.request(method, path, key=new_key(), **kw)
    expect_error(resp, 404, "not_found")  # D21


def test_me_has_no_hold_fields(world):
    me = world.ada.me()
    assert not {"available", "held", "total"} & set(me), me


def test_payments_have_no_later_stage_fields(world):
    p = expect(world.ada.pay("bob", 1), 201).json()
    assert not {"authorization_id", "refund_of", "effective_at", "recorded_at",
                "revision"} & set(p), p


def test_me_ignores_as_of(world):
    """as_of is a stage-3 parameter; here it is an unknown query parameter."""
    expect(world.ada.pay("bob", 1), 201)
    me = expect(world.ada.get("/me", params={"as_of": "2000-01-01T00:00:00+00:00"}), 200).json()
    assert me["balance"] == 9_999


def test_no_browser_product_at_root(world, control):
    resp = control.get("/")
    assert not (resp.status_code == 200 and "data-testid" in resp.text), \
        "stage 2 screens must not exist in the stage-1 folder"


def test_no_html_ui_even_when_asked(world, control):
    """[R35] GET / and GET /requests with Accept: text/html serve no HTML."""
    root = control.get("/", headers={"Accept": "text/html"})
    assert "text/html" not in root.headers.get("content-type", ""), root.status_code
    rq = world.ada.get("/requests", headers={"Accept": "text/html"})
    assert rq.status_code == 200 and rq.headers["content-type"].startswith("application/json")
