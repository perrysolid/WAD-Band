"""S2 'Competing clients and uncertain outcomes': lost payment responses (S2-U5, D37),
latest refresh wins under out-of-order responses (S2-U11, D31), refresh keeps the form,
refusals caused by another client."""
from __future__ import annotations

import pytest
from playwright.sync_api import expect as pw_expect

import pf_model as m
from pf_client import expect
from ui_kit import Writes, absent, fill_pay, is_api_call, log_in, sel, settle, text, wait_wallet


@pytest.fixture
def seeded(reset):
    reset(m.fixture())


def _client(api, handle):
    return api().authenticate(f"{handle}@example.com")


def _first_payment_post(handler):
    """Route handler wrapper: apply `handler` to the first money-moving POST only."""
    state = {"done": False}

    def route(r):
        req = r.request
        if not state["done"] and req.method == "POST" and "/auth/" not in req.url:
            state["done"] = True
            handler(r)
        else:
            r.continue_()
    return route


def _commit_then_lose(r):
    r.fetch()           # the service commits the payment ...
    r.abort("failed")   # ... and the browser never sees the response


def _lose_before_commit(r):
    r.abort("connectionreset")


def _commit_then(status):
    def h(r):
        r.fetch()
        r.fulfill(status=status, content_type="application/json",
                  body='{"error":{"code":"x","message":"gateway"}}')
    return h


LOSSES = {"commit-then-abort": _commit_then_lose, "abort-before-commit": _lose_before_commit,
          "commit-then-503": _commit_then(503), "commit-then-504": _commit_then(504),
          "commit-then-500": _commit_then(500), "commit-then-408": _commit_then(408),
          "commit-then-429": _commit_then(429)}


@pytest.mark.parametrize("loss", list(LOSSES))
def test_lost_payment_response_is_uncertain_and_retry_moves_money_once(seeded, page, api, loss):
    log_in(page)
    page.goto("/")
    wait_wallet(page, 10_000)
    writes = Writes(page)
    page.route("**/*", _first_payment_post(LOSSES[loss]))
    fill_pay(page, handle="bob", amount="15.00", note="lost", visibility="private")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-uncertain"))
    assert text(page, "pay-uncertain").strip(), "pay-uncertain must have text"
    assert page.locator(sel("pay-error")).count() == 0, "an unknown outcome is not a refusal"
    for tid, val in (("pay-handle", "bob"), ("pay-amount", "15.00"), ("pay-note", "lost"),
                     ("pay-visibility", "private")):
        assert page.input_value(sel(tid)) == val, tid
    committed = 0 if loss == "abort-before-commit" else 1_500
    assert _client(api, "ada").balance() == 10_000 - committed
    # retry the unchanged form
    page.click(sel("pay-submit"))
    wait_wallet(page, 8_500)
    absent(page, "pay-uncertain")
    absent(page, "pay-error")
    feed = _client(api, "ada").feed()
    assert [p["amount"] for p in feed] == [1_500], feed
    page.wait_for_selector(sel(f"activity-item-{feed[0]['payment_id']}"))
    pays = writes.to("/payments")
    if pays:
        assert len(pays) == 2, pays
        assert pays[0]["headers"].get("idempotency-key") == pays[1]["headers"].get(
            "idempotency-key"), "the retry must reuse the key"
        assert pays[0]["body"] == pays[1]["body"], "the retry must resend the same body"


def test_uncertain_then_retry_after_more_clicks_still_once(seeded, page, api):
    log_in(page)
    page.goto("/")
    page.route("**/*", _first_payment_post(_commit_then_lose))
    fill_pay(page, amount="2.00")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-uncertain"))
    page.click(sel("pay-submit"))
    page.click(sel("pay-submit"))
    settle(page)
    wait_wallet(page, 9_800)
    assert len(_client(api, "ada").feed()) == 1


def test_uncertain_and_error_look_different(seeded, page):
    """[S2 visual direction] uncertain and refused states are visually distinct."""
    log_in(page)
    page.goto("/")
    page.route("**/*", _first_payment_post(_commit_then_lose))
    fill_pay(page, amount="1.00")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-uncertain"))
    style = "e => { const s = getComputedStyle(e); " \
            "return [s.color, s.backgroundColor, s.borderColor].join('|'); }"
    uncertain = page.eval_on_selector(sel("pay-uncertain"), style)
    page.unroute("**/*")
    page.fill(sel("pay-handle"), "nobody")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-error"))
    absent(page, "pay-uncertain")
    refused = page.eval_on_selector(sel("pay-error"), style)
    assert uncertain != refused, (uncertain, refused)


def test_errors_and_uncertainty_are_announced(seeded, page):
    """[S2-U14] aria-live (or an alert/status role) on pay-error and pay-uncertain."""
    log_in(page)
    page.goto("/")
    page.route("**/*", _first_payment_post(_commit_then_lose))
    fill_pay(page, amount="1.00")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-uncertain"))
    live = """e => { for (let n = e; n; n = n.parentElement) {
                 if (n.getAttribute && (n.getAttribute('aria-live') ||
                     ['alert', 'status'].includes(n.getAttribute('role')))) return true; }
               return false; }"""
    assert page.eval_on_selector(sel("pay-uncertain"), live)
    page.unroute("**/*")
    page.fill(sel("pay-handle"), "nobody")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-error"))
    assert page.eval_on_selector(sel("pay-error"), live)


# ---- latest refresh wins (S2-U11, D31) ----------------------------------------------------

class Holder:
    """Holds the first API response matching `path` while letting later ones through."""

    def __init__(self, page, base_url: str, path: str):
        self.held: list = []
        self.armed = False
        self.path = path
        self.origin = base_url
        page.route("**/*", self._route)

    def _route(self, r):
        req = r.request
        if (self.armed and not self.held and req.method == "GET"
                and is_api_call(req, self.origin) and req.url.split("?")[0].endswith(self.path)):
            self.held.append((r, r.fetch()))       # read now, deliver later
            return
        r.continue_()

    def release(self):
        for r, resp in self.held:
            r.fulfill(response=resp)


@pytest.mark.parametrize("path", ["/me", "/activity"])
def test_a_delayed_earlier_read_never_overwrites_a_later_refresh(seeded, page, api,
                                                                  base_url, path):
    log_in(page)
    page.goto("/")
    wait_wallet(page, 10_000)
    holder = Holder(page, base_url, path)
    holder.armed = True
    page.click(sel("wallet-refresh"))                # refresh #1: response held (old state)
    for _ in range(50):
        if holder.held:
            break
        page.wait_for_timeout(100)
    assert holder.held, f"wallet-refresh did not GET {path}; cannot hold an earlier read"
    p = expect(_client(api, "ada").pay("bob", 300), 201).json()
    page.click(sel("wallet-refresh"))                # refresh #2: sees the payment
    wait_wallet(page, 9_700)
    page.wait_for_selector(sel(f"activity-item-{p['payment_id']}"))
    holder.release()                                  # the stale read arrives last
    settle(page, 800)
    wait_wallet(page, 9_700)
    pw_expect(page.locator(sel(f"activity-item-{p['payment_id']}"))).to_have_count(1)


def test_stale_read_after_own_payment_does_not_resurrect_the_old_balance(seeded, page,
                                                                         base_url):
    """The post-action refresh is later than a refresh issued before the payment."""
    log_in(page)
    page.goto("/")
    wait_wallet(page, 10_000)
    holder = Holder(page, base_url, "/me")
    holder.armed = True
    page.click(sel("wallet-refresh"))
    for _ in range(50):
        if holder.held:
            break
        page.wait_for_timeout(100)
    assert holder.held
    fill_pay(page, amount="1.00")
    page.click(sel("pay-submit"))
    wait_wallet(page, 9_900)
    holder.release()
    settle(page, 800)
    wait_wallet(page, 9_900)


def test_refresh_keeps_the_pay_form(seeded, page, api):
    log_in(page)
    page.goto("/")
    fill_pay(page, handle="cy", amount="3.21", note="keep me", visibility="private")
    expect(_client(api, "bob").pay("ada", 100), 201)
    page.click(sel("wallet-refresh"))
    wait_wallet(page, 10_100)
    assert (page.input_value(sel("pay-handle")), page.input_value(sel("pay-amount")),
            page.input_value(sel("pay-note")), page.input_value(sel("pay-visibility"))) == \
        ("cy", "3.21", "keep me", "private")


def test_refresh_observes_new_holds_and_releases(seeded, page, api):
    log_in(page)
    page.goto("/")
    ada = _client(api, "ada")
    a = expect(ada.authorize("bob", 1_234), 201).json()
    page.click(sel("wallet-refresh"))
    wait_wallet(page, 10_000, held=1_234)
    expect(ada.void(a["authorization_id"]), 200)
    page.click(sel("wallet-refresh"))
    wait_wallet(page, 10_000)


# ---- refusals caused by another client -------------------------------------------------------

def test_balance_spent_elsewhere_refuses_and_refreshes(seeded, page, api):
    """A refused payment shows pay-error, refreshes balance/feed, keeps every input."""
    log_in(page, email="cy@example.com")
    page.goto("/")
    wait_wallet(page, 500)
    other = expect(_client(api, "cy").pay("bob", 400), 201).json()
    fill_pay(page, handle="ada", amount="2.00", note="n", visibility="private")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-error"))
    wait_wallet(page, 100)
    page.wait_for_selector(sel(f"activity-item-{other['payment_id']}"))
    assert (page.input_value(sel("pay-handle")), page.input_value(sel("pay-amount")),
            page.input_value(sel("pay-note")), page.input_value(sel("pay-visibility"))) == \
        ("ada", "2.00", "n", "private")
    absent(page, "pay-uncertain")


def test_balance_held_elsewhere_refuses_and_refreshes(seeded, page, api):
    log_in(page, email="cy@example.com")
    page.goto("/")
    wait_wallet(page, 500)
    expect(_client(api, "cy").authorize("bob", 450), 201)
    fill_pay(page, handle="ada", amount="1.00")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-error"))
    wait_wallet(page, 500, held=450)
