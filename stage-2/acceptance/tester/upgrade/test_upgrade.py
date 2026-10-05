"""S2 'Existing clients after an upgrade' / S2-R13 / S2-U12 / D34: state exported by the
accepted stage-1 service imports into the stage-2 service. Tokens, logins, receipts, pending
requests, failed keys, operators and id sequences survive; a browser signed in before the
upgrade stays signed in and recovers a payment whose response was lost before the export.

Needs BASE_URL (stage 2) and PREVIOUS_BASE_URL (stage 1).

Browser model: the stage-2 UI is loaded from the stage-2 origin, and until the upgrade
every API call it makes (fetch/XHR) is forwarded to the stage-1 service, so the browser's
token and its lost payment really come from stage 1. After stage-1 export -> stage-2 import
the forwarding stops and the same tab talks to stage 2.
"""
from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
import pytest
from playwright.sync_api import expect as pw_expect

import pf_model as m
from conftest import World, check_me
from pf_client import Api, expect, expect_error, new_key
from ui_kit import fill_pay, is_api_call, log_in, sel, settle, text, wait_amount, wait_wallet


def T(frm, to, amount, **extra):
    return {"from_handle": frm, "to_handle": to, "amount": amount, **extra}


@pytest.fixture
def prev(previous_base_url):
    c = httpx.Client(base_url=previous_base_url, timeout=10.0)
    yield c
    c.close()


@pytest.fixture
def cur(base_url):
    c = httpx.Client(base_url=base_url, timeout=10.0)
    yield c
    c.close()


def _post_json(client, path, body):
    return client.post(path, content=json.dumps(body),
                       headers={"Content-Type": "application/json"})


FX = m.fixture([m.ADA, m.BOB, m.CY, m.user("dee", 0)], operators=["u_ada"])


@pytest.fixture
def old(prev, previous_base_url, api, reset):
    """A stage-1 world with every kind of record, plus a payment whose response was 'lost'."""
    reset(m.fixture([m.user("zed", 1)]))              # destination starts different
    expect(_post_json(prev, "/_test/reset", FX), 204)
    w = World(FX, lambda: api(url=previous_base_url))
    rec: dict = {"keys": {}}
    k = new_key()
    body = {"to_handle": "bob", "amount": 1_500, "note": "dinner \U0001F37D",
            "visibility": "private"}
    rec["payment"] = expect(w.ada.post("/payments", json=body, key=k), 201).json()
    rec["keys"]["payment"] = (k, body)
    k = new_key()
    lost = {"to_handle": "cy", "amount": 250, "note": "lost"}
    expect(w.ada.post("/payments", json=lost, key=k), 201)      # response never read
    rec["keys"]["lost"] = (k, lost)
    k = new_key()
    rec["pending"] = expect(w.bob.ask("ada", 1_200, note="taxi", key=k), 201).json()
    rec["keys"]["request"] = (k, {"payer_handle": "ada", "amount": 1_200, "note": "taxi"})
    paid = expect(w.cy.ask("ada", 70), 201).json()
    k = new_key()
    rec["paid"] = expect(w.ada.pay_request(paid["request_id"], {}, key=k), 201).json()
    rec["keys"]["pay"] = (k, paid["request_id"])
    k = new_key()
    rec["split"] = expect(w.ada.split(1_000, ["ada", "bob", "cy"], key=k), 201).json()
    rec["keys"]["split"] = (k, {"amount": 1_000, "participant_handles": ["ada", "bob", "cy"]})
    k = new_key()
    st_body = {"transfers": [T("bob", "dee", 100), T("dee", "cy", 40)]}
    rec["settlement"] = expect(w.ada.post("/settlements", json=st_body, key=k), 201).json()
    rec["keys"]["settlement"] = (k, st_body)
    k = new_key()
    expect_error(w.cy.pay("bob", 99_999, key=k), 409, "insufficient_funds")
    rec["keys"]["failed"] = k
    signup = expect(api(url=previous_base_url).signup("eve@example.com", "eve password",
                                                       "Eve"), 201).json()
    rec["eve_token"] = signup["token"]
    rec["balances"] = w.balances()
    rec["feeds"] = {h: c.feed() for h, c in w.clients.items()}
    rec["requests"] = {h: c.requests_list() for h, c in w.clients.items()}
    rec["tokens"] = {h: c.token for h, c in w.clients.items()}
    rec["export"] = expect(prev.get("/_test/export"), 200).json()
    return w, rec


def _upgrade(cur, snapshot) -> None:
    expect(_post_json(cur, "/_test/import", snapshot), 204)


def _same_instant(a: str, b: str) -> bool:
    return m.parse(a) == m.parse(b)


def _same_record(old: dict, new: dict) -> None:
    """New may add stage-2 fields; every stage-1 field is the same (timestamps by instant)."""
    for k, v in old.items():
        if k in ("created_at", "committed_at") and isinstance(v, str):
            assert _same_instant(v, new[k]), (k, v, new[k])
        else:
            assert new.get(k) == v, (k, v, new.get(k))


def test_stage1_export_imports_and_tokens_keep_working(old, cur, api):
    w, rec = old
    _upgrade(cur, rec["export"])
    for h, tok in rec["tokens"].items():
        me = expect(api(token=tok).get("/me"), 200).json()
        check_me(me, h)
        assert me["total"] == rec["balances"][h] and me["held"] == 0
        assert me["available"] == me["total"]
    eve = expect(api(token=rec["eve_token"]).get("/me"), 200).json()
    assert eve["handle"] == "eve" and eve["total"] == 0
    for h in ("ada", "bob", "cy", "dee"):
        api().authenticate(f"{h}@example.com")
    api().authenticate("eve@example.com", "eve password")
    expect_error(api().login("zed@example.com"), 401, "unauthenticated")


def test_records_survive_with_their_identities_and_timestamps(old, cur, api):
    w, rec = old
    _upgrade(cur, rec["export"])
    for h, tok in rec["tokens"].items():
        c = api(token=tok)
        feed = c.feed()
        assert [p["payment_id"] for p in feed] == [p["payment_id"] for p in rec["feeds"][h]]
        for o, n in zip(rec["feeds"][h], feed):
            _same_record(o, n)
            assert n["authorization_id"] is None
        rqs = c.requests_list()
        assert [r["request_id"] for r in rqs] == [r["request_id"] for r in rec["requests"][h]]
        for o, n in zip(rec["requests"][h], rqs):
            _same_record(o, n)
        assert c.auths() == []


def test_receipts_replay_with_the_original_body(old, cur, api):
    """[§7, §10] a stage-1 receipt replays on stage 2 with the identical original body."""
    w, rec = old
    _upgrade(cur, rec["export"])
    ada = api(token=rec["tokens"]["ada"])
    bob = api(token=rec["tokens"]["bob"])
    k, body = rec["keys"]["payment"]
    assert expect(ada.post("/payments", json=body, key=k), 200).json() == rec["payment"]
    expect_error(ada.post("/payments", json={**body, "amount": 1}, key=k), 409,
                 "idempotency_key_reuse")
    k, body = rec["keys"]["request"]
    assert expect(bob.post("/requests", json=body, key=k), 200).json() == rec["pending"]
    k, rid = rec["keys"]["pay"]
    assert expect(ada.pay_request(rid, {}, key=k), 200).json() == rec["paid"]
    k, body = rec["keys"]["split"]
    assert expect(ada.post("/splits", json=body, key=k), 200).json() == rec["split"]
    k, body = rec["keys"]["settlement"]
    assert expect(ada.post("/settlements", json=body, key=k), 200).json() == rec["settlement"]
    assert {h: api(token=t).balance() for h, t in rec["tokens"].items()} == rec["balances"]


def test_lost_response_payment_is_recovered_once(old, cur, api):
    w, rec = old
    _upgrade(cur, rec["export"])
    ada = api(token=rec["tokens"]["ada"])
    k, body = rec["keys"]["lost"]
    got = expect(ada.post("/payments", json=body, key=k), 200).json()
    assert got["amount"] == 250 and got["to_handle"] == "cy"
    assert ada.balance() == rec["balances"]["ada"]
    assert sum(1 for p in ada.feed() if p["payment_id"] == got["payment_id"]) == 1


def test_failed_key_stays_free_and_new_work_continues(old, cur, api):
    w, rec = old
    _upgrade(cur, rec["export"])
    cy = api(token=rec["tokens"]["cy"])
    ada = api(token=rec["tokens"]["ada"])
    expect(cy.pay("bob", 10, key=rec["keys"]["failed"]), 201)
    rid = rec["pending"]["request_id"]
    p = expect(ada.pay_request(rid, {"visibility": "private"}), 201).json()
    assert p["request_id"] == rid and p["authorization_id"] is None
    old_ids = {x["payment_id"] for f in rec["feeds"].values() for x in f}
    assert p["payment_id"] not in old_ids
    rq2 = expect(ada.ask("bob", 5), 201).json()
    assert rq2["request_id"] not in {r["request_id"] for rs in rec["requests"].values()
                                     for r in rs}
    a = expect(ada.authorize("bob", 100), 201).json()
    assert m.parse(a["expires_at"]) - m.parse(a["created_at"]) == timedelta(seconds=600)
    expect(api(token=rec["tokens"]["bob"]).capture(a["authorization_id"], {}), 201)
    expect(ada.settle([T("bob", "dee", 1)]), 201)        # operator permission survived
    expect_error(api(token=rec["tokens"]["bob"]).settle([T("ada", "dee", 1)]), 403,
                 "forbidden")


def test_import_is_repeatable_and_replaces(old, cur, api):
    w, rec = old
    _upgrade(cur, rec["export"])
    ada = api(token=rec["tokens"]["ada"])
    expect(ada.pay("bob", 1), 201)
    expect(ada.authorize("bob", 1), 201)
    _upgrade(cur, rec["export"])
    _upgrade(cur, rec["export"])
    assert ada.balance() == rec["balances"]["ada"] and ada.auths() == []
    assert len(ada.feed()) == len(rec["feeds"]["ada"])


def test_stage2_export_of_upgraded_state_round_trips(old, cur, api):
    w, rec = old
    _upgrade(cur, rec["export"])
    ada = api(token=rec["tokens"]["ada"])
    expect(ada.authorize("bob", 321), 201)
    snap = expect(cur.get("/_test/export"), 200).json()
    assert snap["track"] == "pocketful" and snap["format_version"] == 1
    _upgrade(cur, snap)
    assert ada.me()["held"] == 321
    k, body = rec["keys"]["payment"]
    assert expect(ada.post("/payments", json=body, key=k), 200).json() == rec["payment"]


def test_mutated_stage1_exports_are_refused_unchanged(old, cur, api):
    w, rec = old
    _upgrade(cur, rec["export"])
    ada = api(token=rec["tokens"]["ada"])
    expect(ada.pay("bob", 3), 201)
    before = ada.balance()
    for mutate in (lambda s: s.__setitem__("format_version", 2),
                   lambda s: s.__setitem__("track", "other"),
                   lambda s: s.pop("state")):
        bad = json.loads(json.dumps(rec["export"]))
        mutate(bad)
        expect_error(_post_json(cur, "/_test/import", bad), 422, "validation_failed")
        assert ada.balance() == before


# ---- the browser across the upgrade --------------------------------------------------------

class UpgradeProxy:
    """Forward the page's API calls to stage 1 until `cut()`; optionally lose one response."""

    def __init__(self, page, base_url: str, previous_base_url: str):
        self.origin = "{0.scheme}://{0.netloc}".format(urlsplit(base_url))
        self.prev = previous_base_url.rstrip("/")
        self.forward = True
        self.lose_next_payment = False
        self.forwarded: list[str] = []
        page.route("**/*", self._route)

    def cut(self) -> None:
        self.forward = False

    def _route(self, r) -> None:
        req = r.request
        if not is_api_call(req, self.origin):
            r.continue_()
            return
        path = urlsplit(req.url).path
        losing = self.lose_next_payment and req.method == "POST" and path == "/payments"
        if self.forward:
            self.forwarded.append(f"{req.method} {path}")
            resp = r.fetch(url=self.prev + req.url[len(self.origin):])
            if losing:
                self.lose_next_payment = False
                r.abort("failed")
            else:
                r.fulfill(response=resp)
            return
        r.continue_()


def test_browser_signed_in_before_the_upgrade(reset, cur, prev, page, base_url,
                                              previous_base_url, api):
    """Signed in on stage 1, a lost pay, a pending request; after import: same tab, same
    token, the retry recovers the original payment, the request is payable."""
    reset(m.fixture([m.user("zed", 1)]))
    expect(_post_json(prev, "/_test/reset", FX), 204)
    proxy = UpgradeProxy(page, base_url, previous_base_url)
    log_in(page)
    page.goto("/")
    wait_amount(page, "wallet-balance", 10_000)
    assert any(f.startswith("POST /auth/login") for f in proxy.forwarded), \
        "the UI did not sign in through an API call; cannot model a stage-1 session"
    bob1 = Api(previous_base_url).authenticate("bob@example.com")
    rid = expect(bob1.ask("ada", 1_200, note="taxi"), 201).json()["request_id"]
    proxy.lose_next_payment = True
    fill_pay(page, handle="bob", amount="15.00", note="across", visibility="private")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-uncertain"))
    assert page.locator(sel("pay-error")).count() == 0
    assert Api(previous_base_url).authenticate("ada@example.com").balance() == 8_500

    # the upgrade happens between browser requests
    _upgrade(cur, expect(prev.get("/_test/export"), 200).json())
    proxy.cut()

    page.click(sel("pay-submit"))                         # same key, same body -> 200 replay
    wait_wallet(page, 8_500)
    pw_expect(page.locator(sel("pay-uncertain"))).to_have_count(0)
    pw_expect(page.locator(sel("pay-error"))).to_have_count(0)
    pw_expect(page.locator(sel("current-user"))).to_be_visible()
    ada2 = api().authenticate("ada@example.com")
    assert ada2.balance() == 8_500
    feed = ada2.feed()
    assert [p["amount"] for p in feed] == [1_500]
    page.wait_for_selector(sel(f"activity-item-{feed[0]['payment_id']}"))

    page.goto("/requests")                                # still signed in on stage 2
    pw_expect(page.locator(sel("current-user"))).to_be_visible()
    page.click(sel(f"request-pay-{rid}"))
    page.wait_for_selector(f"{sel('request-item-' + rid)}[data-status='paid']")
    assert ada2.balance() == 7_300
    page.goto("/")
    wait_wallet(page, 7_300)


def test_browser_refresh_after_the_upgrade_shows_the_imported_state(reset, cur, prev, page,
                                                                    base_url,
                                                                    previous_base_url, api):
    reset(m.fixture([m.user("zed", 1)]))
    expect(_post_json(prev, "/_test/reset", FX), 204)
    proxy = UpgradeProxy(page, base_url, previous_base_url)
    log_in(page, email="cy@example.com")
    page.goto("/")
    wait_amount(page, "wallet-balance", 500)
    expect(Api(previous_base_url).authenticate("ada@example.com").pay("cy", 777), 201)
    _upgrade(cur, expect(prev.get("/_test/export"), 200).json())
    proxy.cut()
    page.click(sel("wallet-refresh"))
    wait_wallet(page, 1_277)
    pw_expect(page.locator(sel("current-user"))).to_be_visible()
    assert text(page, "current-handle").strip() == "cy"
    # the stage-2 features work for the upgraded session
    page.goto("/authorizations")
    page.wait_for_selector(sel("empty-authorizations"))
    page.fill(sel("authorize-handle"), "bob")
    page.fill(sel("authorize-amount"), "2.77")
    page.click(sel("authorize-submit"))
    wait_wallet(page, 1_277, held=277)
    settle(page, 100)


# ---- D38 variant: stage-2 A -> stage-2 B ---------------------------------------------------

class CutoverProxy:
    """API calls go to the page's own origin (A) until `to(B)`; optionally lose one pay
    response after the server committed it (the request really reaches A)."""

    def __init__(self, page, base_url: str):
        self.origin = "{0.scheme}://{0.netloc}".format(urlsplit(base_url))
        self.target: str | None = None
        self.lose_next_payment = False
        self.sent: list[str] = []
        page.route("**/*", self._route)

    def to(self, other: str) -> None:
        self.target = other.rstrip("/")

    def _route(self, r) -> None:
        req = r.request
        if not is_api_call(req, self.origin):
            r.continue_()
            return
        path = urlsplit(req.url).path
        self.sent.append(f"{req.method} {path}")
        if self.lose_next_payment and req.method == "POST" and path == "/payments":
            self.lose_next_payment = False
            target = self.target or self.origin
            r.fetch(url=target + req.url[len(self.origin):])  # commits on the server
            r.abort("failed")                                  # ...but the browser never hears
            return
        if self.target is None:
            r.continue_()
        else:
            r.fulfill(response=r.fetch(url=self.target + req.url[len(self.origin):]))


def test_browser_survives_a_stage2_to_stage2_import(reset, cur, page, base_url, api,
                                                    second_base_url):
    """[D38, S2-U12] signed in on A with a lost pay response; export A, import into a fresh
    B, route the same tab to B: still signed in, the unchanged retry replays (200) the
    original payment, money moved once, and the wallet shows B's imported balance.
    B is SECOND_BASE_URL when given; otherwise A itself is wiped by a reset and then
    receives the import, which is the same 'fresh destination' contract."""
    fx = m.fixture([m.ADA, m.BOB, m.CY], authorizations=[
        m.hold("a_seed", "ada", "cy", 1_000)])
    reset(fx)
    proxy = CutoverProxy(page, base_url)
    log_in(page)
    page.goto("/")
    wait_wallet(page, 10_000, held=1_000)
    proxy.lose_next_payment = True
    fill_pay(page, handle="bob", amount="15.00", note="A to B", visibility="private")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-uncertain"))
    assert page.locator(sel("pay-error")).count() == 0
    assert api().authenticate("ada@example.com").balance() == 8_500   # it did commit on A

    snapshot = expect(cur.get("/_test/export"), 200).json()
    dest_url = second_base_url or base_url
    dest = httpx.Client(base_url=dest_url, timeout=10.0)
    try:
        expect(_post_json(dest, "/_test/reset", m.fixture([m.user("zed", 1)])), 204)
        _upgrade(dest, snapshot)
    finally:
        dest.close()
    if second_base_url:
        proxy.to(second_base_url)
        reset(m.fixture([m.user("zed", 1)]))       # A no longer knows ada at all

    page.click(sel("pay-submit"))                    # same key and body -> 200 replay on B
    wait_wallet(page, 8_500, held=1_000)
    pw_expect(page.locator(sel("pay-uncertain"))).to_have_count(0)
    pw_expect(page.locator(sel("pay-error"))).to_have_count(0)
    pw_expect(page.locator(sel("current-user"))).to_be_visible()
    assert any(s == "POST /payments" for s in proxy.sent[-6:]), proxy.sent
    on_b = Api(dest_url).authenticate("ada@example.com")
    feed = on_b.feed()
    assert [(p["amount"], p["note"], p["visibility"]) for p in feed] == \
        [(1_500, "A to B", "private")], "the retry must recover the original, not pay again"
    page.wait_for_selector(sel(f"activity-item-{feed[0]['payment_id']}"))
    me = on_b.me()
    check_me(me, "ada")
    assert (me["total"], me["held"]) == (8_500, 1_000)
    on_b.close()
