"""S2-U10 the /authorizations screen and the authorize form; S2-U3 held/available."""
from __future__ import annotations

import time
from datetime import timedelta

import pytest
from playwright.sync_api import expect as pw_expect

import pf_model as m
from pf_client import expect
from ui_kit import (absent, fill_authorize, log_in, sel, settle, text, wait_wallet)


def _client(api, handle):
    return api().authenticate(f"{handle}@example.com")


def _status(page, aid):
    return page.get_attribute(sel(f"authorization-item-{aid}"), "data-status")


@pytest.fixture
def mixed(reset):
    past = m.iso(m.now() - timedelta(hours=2))
    reset(m.fixture(authorizations=[
        m.hold("a_in", "bob", "ada", 1_500, note="deposit"),
        m.hold("a_out", "ada", "cy", 2_000),
        m.hold("a_cap", "ada", "bob", 700, status="captured"),
        m.hold("a_void", "bob", "ada", 300, status="voided"),
        m.hold("a_exp", "ada", "bob", 50, status="expired"),
        m.hold("a_clock", "cy", "ada", 25, expires_at=past)]))


def test_seeded_holds_render_with_status_amount_and_expiry(mixed, page, api):
    log_in(page)
    page.goto("/authorizations")
    page.wait_for_selector(sel("authorization-item-a_in"))
    listed = {a["authorization_id"]: a for a in _client(api, "ada").auths()}
    for aid, status in (("a_in", "open"), ("a_out", "open"), ("a_cap", "captured"),
                        ("a_void", "voided"), ("a_exp", "expired"), ("a_clock", "expired")):
        assert _status(page, aid) == status, aid
        assert text(page, f"authorization-amount-{aid}").strip() == \
            m.money(listed[aid]["amount"]), aid
        assert text(page, f"authorization-expires-{aid}").strip() == listed[aid]["expires_at"]
    assert text(page, "authorization-captured-a_cap").strip() == "7.00 EUR"
    for aid in ("a_in", "a_out", "a_void", "a_exp", "a_clock"):
        assert page.locator(sel(f"authorization-captured-{aid}")).count() == 0, aid
    wait_wallet(page, 10_000, held=2_000)


def test_buttons_only_where_allowed(mixed, page):
    log_in(page)
    page.goto("/authorizations")
    page.wait_for_selector(sel("authorization-item-a_in"))
    pw_expect(page.locator(sel("authorization-capture-a_in"))).to_be_visible()
    pw_expect(page.locator(sel("authorization-capture-amount-a_in"))).to_have_value("15.00")
    assert page.locator(sel("authorization-void-a_in")).count() == 0
    pw_expect(page.locator(sel("authorization-void-a_out"))).to_be_visible()
    assert page.locator(sel("authorization-capture-a_out")).count() == 0
    assert page.locator(sel("authorization-capture-amount-a_out")).count() == 0
    for aid in ("a_cap", "a_void", "a_exp", "a_clock"):
        for part in ("capture", "capture-amount", "void"):
            assert page.locator(sel(f"authorization-{part}-{aid}")).count() == 0, (part, aid)


def test_list_is_newest_first(reset, page, api):
    reset(m.fixture())
    ada = _client(api, "ada")
    made = [expect(ada.authorize(h, 100 + i), 201).json()["authorization_id"]
            for i, h in enumerate(["bob", "cy", "bob"])]
    made.append(expect(_client(api, "bob").authorize("ada", 5), 201).json()["authorization_id"])
    log_in(page)
    page.goto("/authorizations")
    page.wait_for_selector(sel(f"authorization-item-{made[-1]}"))
    order = page.eval_on_selector_all(
        f"{sel('authorization-list')} [data-testid^='authorization-item-']",
        "els => els.map(e => e.getAttribute('data-testid'))")
    assert order == [f"authorization-item-{a}" for a in reversed(made)]


def test_empty_authorizations(reset, page):
    reset(m.fixture())
    log_in(page)
    page.goto("/authorizations")
    page.wait_for_selector(sel("empty-authorizations"))


def test_authorize_form_creates_one_hold_and_updates_the_wallet(reset, page, api):
    reset(m.fixture())
    log_in(page)
    page.goto("/authorizations")
    fill_authorize(page, handle="bob", amount="20.00", note="deposit", visibility="private")
    page.click(sel("authorize-submit"))
    wait_wallet(page, 10_000, held=2_000)
    (a,) = _client(api, "ada").auths()
    assert (a["amount"], a["note"], a["visibility"], a["status"]) == \
        (2_000, "deposit", "private", "open")
    page.wait_for_selector(sel(f"authorization-item-{a['authorization_id']}"))
    pw_expect(page.locator(sel(f"authorization-void-{a['authorization_id']}"))).to_be_visible()
    absent(page, "empty-authorizations")
    page.click(sel("authorize-submit"))      # unchanged: a replay, not a second hold
    settle(page)
    assert len(_client(api, "ada").auths()) == 1
    wait_wallet(page, 10_000, held=2_000)
    absent(page, "authorize-error")
    assert page.input_value(sel("authorize-amount")) == "20.00"


def test_authorize_form_is_on_the_home_screen_too(reset, page, api):
    """[D30] the 'Hold money' card on / uses the same testids."""
    reset(m.fixture())
    log_in(page)
    page.goto("/")
    fill_authorize(page, handle="cy", amount="1.25")
    page.click(sel("authorize-submit"))
    wait_wallet(page, 10_000, held=125)


@pytest.mark.parametrize("handle,amount,why", [("bob", "100.01", "insufficient available"),
                                               ("nobody", "1.00", "unknown"),
                                               ("ada", "1.00", "self"),
                                               ("bob", "1.001", "too many places"),
                                               ("bob", "x", "nonnumeric")])
def test_authorize_refusals(reset, page, api, handle, amount, why):
    reset(m.fixture())
    log_in(page)
    page.goto("/authorizations")
    fill_authorize(page, handle=handle, amount=amount)
    if why == "insufficient available":
        expect(_client(api, "ada").authorize("cy", 9_900), 201)
        page.click(sel("wallet-refresh"))
        wait_wallet(page, 10_000, held=9_900)
    page.click(sel("authorize-submit"))
    page.wait_for_selector(sel("authorize-error"))
    assert len(_client(api, "ada").auths()) == (1 if why == "insufficient available" else 0)


def test_capture_from_the_list(mixed, page, api):
    log_in(page)
    page.goto("/authorizations")
    page.click(sel("authorization-capture-a_in"))
    pw_expect(page.locator(sel("authorization-item-a_in"))).to_have_attribute("data-status",
                                                                            "captured")
    pw_expect(page.locator(sel("authorization-captured-a_in"))).to_have_text("15.00 EUR")
    assert page.locator(sel("authorization-capture-a_in")).count() == 0
    wait_wallet(page, 11_500, held=2_000)
    assert _client(api, "bob").balance() == 1_000


def test_partial_capture_from_the_list(mixed, page, api):
    log_in(page)
    page.goto("/authorizations")
    page.fill(sel("authorization-capture-amount-a_in"), "4.50")
    page.click(sel("authorization-capture-a_in"))
    pw_expect(page.locator(sel("authorization-item-a_in"))).to_have_attribute("data-status",
                                                                            "captured")
    pw_expect(page.locator(sel("authorization-captured-a_in"))).to_have_text("4.50 EUR")
    assert _client(api, "bob").me()["available"] == 2_500 - 450
    wait_wallet(page, 10_450, held=2_000)


def test_prefill_is_the_remaining_amount_after_partial_captures(mixed, page, api):
    expect(_client(api, "ada").capture("a_in", {"amount": 501, "final": False}), 201)
    log_in(page)
    page.goto("/authorizations")
    pw_expect(page.locator(sel("authorization-capture-amount-a_in"))).to_have_value("9.99")
    assert _status(page, "a_in") == "open"


@pytest.mark.parametrize("typed", ["15.01", "abc", "1.001", "0"])
def test_invalid_or_excessive_capture_amounts_are_refused(mixed, page, api, typed):
    log_in(page)
    page.goto("/authorizations")
    page.fill(sel("authorization-capture-amount-a_in"), typed)
    page.click(sel("authorization-capture-a_in"))
    page.wait_for_selector(sel("authorization-error"))
    assert _client(api, "ada").auth("a_in")["status"] == "open"
    assert _client(api, "ada").balance() == 10_000


def test_void_from_the_list(mixed, page, api):
    log_in(page)
    page.goto("/authorizations")
    wait_wallet(page, 10_000, held=2_000)
    page.click(sel("authorization-void-a_out"))
    pw_expect(page.locator(sel("authorization-item-a_out"))).to_have_attribute("data-status",
                                                                             "voided")
    assert page.locator(sel("authorization-void-a_out")).count() == 0
    wait_wallet(page, 10_000)


def test_capture_of_a_hold_voided_elsewhere_shows_error_and_refreshes(mixed, page, api):
    log_in(page)
    page.goto("/authorizations")
    pw_expect(page.locator(sel("authorization-capture-a_in"))).to_be_visible()
    expect(_client(api, "bob").void("a_in"), 200)
    page.click(sel("authorization-capture-a_in"))
    page.wait_for_selector(sel("authorization-error"))
    pw_expect(page.locator(sel("authorization-capture-a_in"))).to_have_count(0)
    pw_expect(page.locator(sel("authorization-item-a_in"))).to_have_attribute("data-status",
                                                                            "voided")


def test_void_of_a_hold_captured_elsewhere_shows_error_and_refreshes(mixed, page, api):
    log_in(page)
    page.goto("/authorizations")
    pw_expect(page.locator(sel("authorization-void-a_out"))).to_be_visible()
    expect(_client(api, "cy").capture("a_out", {}), 201)
    page.click(sel("authorization-void-a_out"))
    page.wait_for_selector(sel("authorization-error"))
    pw_expect(page.locator(sel("authorization-void-a_out"))).to_have_count(0)
    pw_expect(page.locator(sel("authorization-captured-a_out"))).to_have_text("20.00 EUR")
    wait_wallet(page, 8_000)


def test_expired_hold_shows_expired_after_a_refresh(reset, page, api):
    reset(m.fixture(ttl=2))
    log_in(page)
    page.goto("/authorizations")
    fill_authorize(page, handle="bob", amount="30.00")
    page.click(sel("authorize-submit"))
    wait_wallet(page, 10_000, held=3_000)
    (a,) = _client(api, "ada").auths()
    time.sleep(max(0.0, (m.parse(a["expires_at"]) - m.now()).total_seconds() + 0.4))
    page.click(sel("wallet-refresh"))
    wait_wallet(page, 10_000)
    page.goto("/authorizations")
    pw_expect(page.locator(sel(f"authorization-item-{a['authorization_id']}"))) \
        .to_have_attribute("data-status", "expired")
    assert page.locator(sel(f"authorization-void-{a['authorization_id']}")).count() == 0


def test_capture_of_an_expired_hold_shows_error(reset, page, api):
    reset(m.fixture(ttl=3))
    a = expect(_client(api, "bob").authorize("ada", 100), 201).json()
    log_in(page)
    page.goto("/authorizations")
    pw_expect(page.locator(sel(f"authorization-capture-{a['authorization_id']}"))).to_be_visible()
    time.sleep(max(0.0, (m.parse(a["expires_at"]) - m.now()).total_seconds() + 0.4))
    page.click(sel(f"authorization-capture-{a['authorization_id']}"))
    page.wait_for_selector(sel("authorization-error"))
    pw_expect(page.locator(sel(f"authorization-item-{a['authorization_id']}"))) \
        .to_have_attribute("data-status", "expired")
    assert _client(api, "ada").balance() == 10_000


def test_jpy_capture_prefill_has_no_decimal_point(reset, page):
    reset(m.fixture(currency="JPY", authorizations=[m.hold("a_1", "bob", "ada", 1_500)]))
    log_in(page)
    page.goto("/authorizations")
    pw_expect(page.locator(sel("authorization-capture-amount-a_1"))).to_have_value("1500")
    assert text(page, "authorization-amount-a_1").strip() == "1500 JPY"
