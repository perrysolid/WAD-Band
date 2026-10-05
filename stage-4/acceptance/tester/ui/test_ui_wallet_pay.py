"""S2-U3 wallet panel, S2-U4 decimal amount input, S2-U5 pay form identity and refusals,
S2-U6 request form."""
from __future__ import annotations

import pytest

import pf_model as m
from ui_kit import (Writes, absent, fill_pay, log_in, present, sel, settle, text,
                    wait_amount, wait_wallet)


@pytest.fixture
def seeded(reset):
    fx = m.fixture()
    reset(fx)
    return fx


def _ada(api):
    return api().authenticate("ada@example.com")


# ---- wallet panel -------------------------------------------------------------------

def test_wallet_without_holds(seeded, page):
    """[S2-U3] total and available exact, with data-amount; held absent at zero."""
    log_in(page)
    page.goto("/")
    wait_wallet(page, 10_000)
    assert text(page, "wallet-balance").strip() == "100.00 EUR"


def test_wallet_with_seeded_holds_right_after_reset(reset, page):
    """[S2-U3, S2-U10] seeded open holds show as held; available is the spending number."""
    reset(m.fixture(authorizations=[m.hold("a_1", "ada", "bob", 2_000),
                                    m.hold("a_2", "ada", "cy", 345),
                                    m.hold("a_3", "ada", "bob", 999, status="voided")]))
    log_in(page)
    page.goto("/")
    wait_wallet(page, 10_000, held=2_345)
    page.goto("/authorizations")
    wait_wallet(page, 10_000, held=2_345)


def test_available_is_the_headline_number(reset, page):
    """[S2-U3, S2 visual direction] available is visibly the most prominent amount."""
    reset(m.fixture(authorizations=[m.hold("a_1", "ada", "bob", 2_000)]))
    log_in(page)
    page.goto("/")
    wait_wallet(page, 10_000, held=2_000)

    def size(tid):
        return page.eval_on_selector(sel(tid), "e => parseFloat(getComputedStyle(e).fontSize)")

    assert size("wallet-available") > size("wallet-balance"), "available must be the headline"
    assert size("wallet-available") > size("wallet-held")


@pytest.mark.parametrize("cur,balance,shown", [("JPY", 10_000, "10000 JPY"),
                                               ("BHD", 10_000, "10.000 BHD"),
                                               ("BHD", 5, "0.005 BHD"),
                                               ("EUR", 7, "0.07 EUR"),
                                               ("EUR", 1_000_000_000_000, "10000000000.00 EUR")])
def test_formatted_amounts_follow_minor_units(reset, page, cur, balance, shown):
    """[S2 Formatted amount, D27] exact minor_units places, no separators, no sign."""
    reset(m.fixture([m.user("ada", balance), m.BOB], currency=cur))
    log_in(page)
    page.goto("/")
    wait_amount(page, "wallet-balance", balance)
    assert text(page, "wallet-balance").strip() == shown
    assert text(page, "wallet-available").strip() == shown


# ---- amount input (S2-U4) --------------------------------------------------------------

@pytest.mark.parametrize("typed,minor", [("15", 1_500), ("15.5", 1_550), ("15.00", 1_500),
                                         ("15.0", 1_500), ("0.01", 1), (" 2.50 ", 250),
                                         ("007.10", 710)])
def test_typed_decimals_become_minor_units(seeded, page, typed, minor):
    log_in(page)
    page.goto("/")
    fill_pay(page, amount=typed)
    page.click(sel("pay-submit"))
    wait_wallet(page, 10_000 - minor)
    absent(page, "pay-error")


@pytest.mark.parametrize("typed", ["15.005", "abc", "-5", "1e3", "15,00", "", "0", "0.00",
                                   "10000000.01", ".", "1.2.3", "+5", "١٥", "NaN",
                                   "Infinity", "0x10"])
def test_invalid_amounts_are_refused_without_a_request(seeded, page, api, typed):
    """[S2-U4, D28] nonnumeric, too many places, out of range: pay-error, nothing sent."""
    log_in(page)
    page.goto("/")
    wait_wallet(page, 10_000)
    writes = Writes(page)
    fill_pay(page, amount=typed)
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-error"))
    settle(page, 300)
    assert writes.money_writes() == [], f"{typed!r} sent {writes.money_writes()}"
    assert _ada(api).balance() == 10_000


@pytest.mark.parametrize("cur,typed,minor,ok", [("JPY", "15", 15, True), ("JPY", "15.0", 0, False),
                                                ("JPY", "15.5", 0, False),
                                                ("BHD", "1.005", 1_005, True),
                                                ("BHD", "1.0005", 0, False),
                                                ("BHD", "2", 2_000, True)])
def test_decimal_rules_follow_the_currency(reset, page, api, cur, typed, minor, ok):
    reset(m.fixture(currency=cur))
    log_in(page)
    page.goto("/")
    wait_amount(page, "wallet-balance", 10_000)
    writes = Writes(page)
    fill_pay(page, amount=typed)
    page.click(sel("pay-submit"))
    if ok:
        wait_amount(page, "wallet-balance", 10_000 - minor)
    else:
        page.wait_for_selector(sel("pay-error"))
        settle(page, 300)
        assert writes.money_writes() == []
        assert _ada(api).balance() == 10_000


# ---- pay form identity (S2-U5) ------------------------------------------------------------

def test_values_are_kept_after_success(seeded, page):
    log_in(page)
    page.goto("/")
    fill_pay(page, handle="bob", amount="15.00", note="dinner \U0001F35D", visibility="private")
    page.click(sel("pay-submit"))
    wait_wallet(page, 8_500)
    assert page.input_value(sel("pay-handle")) == "bob"
    assert page.input_value(sel("pay-amount")) == "15.00"
    assert page.input_value(sel("pay-note")) == "dinner \U0001F35D"
    assert page.input_value(sel("pay-visibility")) == "private"


def test_visibility_select_offers_exactly_public_and_private(seeded, page):
    log_in(page)
    page.goto("/")
    values = page.eval_on_selector_all(f"{sel('pay-visibility')} option",
                                       "os => os.map(o => o.value)")
    assert sorted(values) == ["private", "public"]


@pytest.mark.parametrize("clicks", ["sequential", "double-click", "triple-burst"])
def test_unchanged_resubmission_moves_money_once(seeded, page, api, clicks):
    """[S2-U5] same key and body: one payment, balance falls once, no pay-error."""
    log_in(page)
    page.goto("/")
    writes = Writes(page)
    fill_pay(page, amount="15.00", note="once")
    if clicks == "sequential":
        page.click(sel("pay-submit"))
        wait_wallet(page, 8_500)
        page.click(sel("pay-submit"))
    elif clicks == "double-click":
        page.dblclick(sel("pay-submit"))
    else:
        for _ in range(3):
            page.click(sel("pay-submit"), no_wait_after=True)
    settle(page)
    wait_wallet(page, 8_500)
    absent(page, "pay-error")
    feed = _ada(api).feed()
    assert [p["amount"] for p in feed] == [1_500], feed
    pays = writes.to("/payments")
    if pays:  # a client-side form: every send reused one key and one body
        assert len({w["headers"].get("idempotency-key") for w in pays}) == 1, pays
        assert all(w["body"] == pays[0]["body"] for w in pays)


@pytest.mark.parametrize("field,value", [("pay-amount", "20.00"), ("pay-note", "again"),
                                         ("pay-handle", "cy")])
def test_changing_a_field_makes_a_new_payment(seeded, page, api, field, value):
    log_in(page)
    page.goto("/")
    fill_pay(page, amount="15.00")
    page.click(sel("pay-submit"))
    wait_wallet(page, 8_500)
    page.fill(sel(field), value)
    page.click(sel("pay-submit"))
    second = 2_000 if field == "pay-amount" else 1_500
    wait_wallet(page, 8_500 - second)
    absent(page, "pay-error")
    assert len(_ada(api).feed()) == 2


def test_changing_visibility_makes_a_new_payment(seeded, page, api):
    log_in(page)
    page.goto("/")
    fill_pay(page, amount="1.00", visibility="public")
    page.click(sel("pay-submit"))
    wait_wallet(page, 9_900)
    page.select_option(sel("pay-visibility"), "private")
    page.click(sel("pay-submit"))
    wait_wallet(page, 9_800)
    assert sorted(p["visibility"] for p in _ada(api).feed()) == ["private", "public"]


def test_insufficient_funds_shows_pay_error_and_keeps_inputs(seeded, page, api):
    log_in(page, email="cy@example.com")
    page.goto("/")
    fill_pay(page, handle="bob", amount="99.00", note="too much", visibility="private")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-error"))
    assert text(page, "pay-error").strip()
    assert present(page, "pay-uncertain") is False
    assert page.input_value(sel("pay-handle")) == "bob"
    assert page.input_value(sel("pay-amount")) == "99.00"
    assert page.input_value(sel("pay-note")) == "too much"
    assert page.input_value(sel("pay-visibility")) == "private"
    wait_wallet(page, 500)


@pytest.mark.parametrize("handle", ["nobody", "ada"])
def test_refused_recipients_show_pay_error(seeded, page, api, handle):
    log_in(page)
    page.goto("/")
    fill_pay(page, handle=handle, amount="1.00")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-error"))
    assert _ada(api).balance() == 10_000


def test_pay_error_clears_after_a_later_success(seeded, page):
    log_in(page)
    page.goto("/")
    fill_pay(page, handle="nobody", amount="1.00")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-error"))
    page.fill(sel("pay-handle"), "bob")
    page.click(sel("pay-submit"))
    wait_wallet(page, 9_900)
    absent(page, "pay-error")


def test_paying_held_money_is_refused_against_available(reset, page, api):
    """[S2-R10 in the UI] the form is judged against available, not total."""
    reset(m.fixture(authorizations=[m.hold("a_1", "cy", "bob", 400)]))
    log_in(page, email="cy@example.com")
    page.goto("/")
    wait_wallet(page, 500, held=400)
    fill_pay(page, amount="1.01")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-error"))
    page.fill(sel("pay-amount"), "1.00")
    page.click(sel("pay-submit"))
    wait_wallet(page, 400, available=0, held=400)


# ---- request form (S2-U6) ---------------------------------------------------------------

def test_request_form_creates_a_request_once(seeded, page, api):
    log_in(page)
    page.goto("/")
    page.fill(sel("request-handle"), "bob")
    page.fill(sel("request-amount"), "12.34")
    page.fill(sel("request-note"), "taxi")
    page.click(sel("request-submit"))
    bob = api().authenticate("bob@example.com")
    for _ in range(50):
        if bob.requests_list():
            break
        page.wait_for_timeout(100)
    page.click(sel("request-submit"))
    settle(page)
    rqs = bob.requests_list()
    assert len(rqs) == 1 and rqs[0]["amount"] == 1_234 and rqs[0]["note"] == "taxi"
    absent(page, "request-error")
    wait_wallet(page, 10_000)


@pytest.mark.parametrize("handle,amount", [("nobody", "1.00"), ("ada", "1.00"),
                                           ("bob", "1.234"), ("bob", "x")])
def test_request_form_refusals(seeded, page, api, handle, amount):
    log_in(page)
    page.goto("/")
    page.fill(sel("request-handle"), handle)
    page.fill(sel("request-amount"), amount)
    page.click(sel("request-submit"))
    page.wait_for_selector(sel("request-error"))
    assert api().authenticate("bob@example.com").requests_list() == []


def test_request_for_more_than_the_payer_holds_is_fine(seeded, page, api):
    log_in(page)
    page.goto("/")
    page.fill(sel("request-handle"), "cy")
    page.fill(sel("request-amount"), "9999.99")
    page.click(sel("request-submit"))
    cy = api().authenticate("cy@example.com")
    for _ in range(50):
        if cy.requests_list():
            break
        page.wait_for_timeout(100)
    assert cy.requests_list()[0]["amount"] == 999_999
    absent(page, "request-error")
