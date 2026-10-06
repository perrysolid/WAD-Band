"""UI upgrade item 4: Pay again / Request again and the split extra-unit explanation.
Test ids per stage-4/RUN.md ('Quick actions and split explanation')."""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect as pw_expect

import pf_model as m
from pf_client import expect
from test_ui_first_click import _stray
from test_ui_polish import _SCAN_JS
from ui_kit import Writes, log_in, sel, settle, wait_wallet


@pytest.fixture
def qa(reset, api):
    reset(m.fixture([m.ADA, m.BOB, m.user("cy", 5_000, display_name="Cy"), m.user("dee", 0, display_name="Dee")]))

    class W:
        pass
    w = W()
    for h in ("ada", "bob", "cy"):
        setattr(w, h, api().authenticate(f"{h}@example.com"))
    w.sent = expect(w.ada.pay("bob", 1_250, note="lunch \U0001F96A", visibility="private"), 201).json()
    w.recv = expect(w.cy.pay("ada", 700, note="thanks"), 201).json()
    return w


def val(page, tid):
    return page.input_value(sel(tid))


def active(page):
    return page.evaluate("() => document.activeElement && document.activeElement.dataset.testid")


def has(page, tid):
    return page.locator(sel(tid)).count() > 0


# ---- where the buttons appear ----------------------------------------------------------------------

def test_buttons_follow_the_direction_of_the_payment(qa, page):
    log_in(page)
    page.goto("/")
    s, r = qa.sent["payment_id"], qa.recv["payment_id"]
    page.wait_for_selector(sel(f"activity-item-{s}"))
    assert has(page, f"activity-pay-again-{s}") and not has(page, f"activity-request-again-{s}")
    assert has(page, f"activity-request-again-{r}") and not has(page, f"activity-pay-again-{r}")


def test_third_party_items_have_no_quick_actions(qa, new_page):
    page = new_page()
    log_in(page, email="dee@example.com")
    page.goto("/")
    page.wait_for_selector(sel(f"activity-item-{qa.recv['payment_id']}"))
    assert page.locator("[data-testid^='activity-pay-again-'], [data-testid^='activity-request-again-']").count() == 0


def test_the_payment_page_offers_the_same_actions(qa, new_page):
    s, r = qa.sent["payment_id"], qa.recv["payment_id"]
    page = new_page()
    log_in(page)
    page.goto(f"/payment/{s}")
    page.wait_for_selector(sel("payment-detail-amount"))
    assert has(page, "payment-detail-pay-again") and not has(page, "payment-detail-request-again")
    page.goto(f"/payment/{r}")
    page.wait_for_selector(sel("payment-detail-amount"))
    assert has(page, "payment-detail-request-again") and not has(page, "payment-detail-pay-again")


# ---- prefill without submitting -----------------------------------------------------------------------

def test_pay_again_prefills_focuses_and_never_submits(qa, page):
    s = qa.sent["payment_id"]
    log_in(page)
    page.goto("/")
    writes = Writes(page)
    page.wait_for_selector(sel(f"activity-pay-again-{s}"))
    page.click(sel(f"activity-pay-again-{s}"))
    pw_expect(page.locator(sel("pay-handle"))).to_have_value("bob")
    assert val(page, "pay-amount") == "12.50"
    assert val(page, "pay-note") == "lunch \U0001F96A"
    assert val(page, "pay-visibility") == "private"
    pw_expect(page.locator(sel("pay-amount"))).to_be_focused()
    page.wait_for_timeout(600)
    assert writes.items == [], f"nothing may be submitted: {writes.items}"
    assert "/payment/" not in page.url
    assert qa.ada.balance() == 10_000 - 1_250 + 700


def test_request_again_prefills_the_counterparty_and_amount(qa, page):
    r = qa.recv["payment_id"]
    log_in(page)
    page.goto("/")
    writes = Writes(page)
    page.wait_for_selector(sel(f"activity-request-again-{r}"))
    page.click(sel(f"activity-request-again-{r}"))
    pw_expect(page.locator(sel("request-handle"))).to_have_value("cy")
    assert val(page, "request-amount") == "7.00"
    assert val(page, "request-note") == "thanks"
    pw_expect(page.locator(sel("request-amount"))).to_be_focused()
    page.wait_for_timeout(600)
    assert writes.items == []
    assert not qa.cy.requests_list()


def test_the_prefilled_form_then_submits_normally(qa, page):
    s = qa.sent["payment_id"]
    log_in(page)
    page.goto("/")
    writes = Writes(page)
    page.wait_for_selector(sel(f"activity-pay-again-{s}"))
    page.click(sel(f"activity-pay-again-{s}"))
    page.click(sel("pay-submit"))
    wait_wallet(page, 10_000 - 2_500 + 700)
    w = writes.to("/payments")
    assert len(w) == 1 and w[0]["body"] == {"to_handle": "bob", "amount": 1_250,
                                            "note": "lunch \U0001F96A", "visibility": "private"}
    assert w[0]["headers"].get("idempotency-key")


def test_editing_the_prefill_makes_a_new_payment(qa, page):
    s = qa.sent["payment_id"]
    log_in(page)
    page.goto("/")
    writes = Writes(page)
    page.wait_for_selector(sel(f"activity-pay-again-{s}"))
    page.click(sel(f"activity-pay-again-{s}"))
    page.fill(sel("pay-amount"), "1.00")
    page.click(sel("pay-submit"))
    wait_wallet(page, 10_000 - 1_250 - 100 + 700)
    assert writes.to("/payments")[0]["body"]["amount"] == 100


def test_clicking_the_button_does_not_open_the_detail(qa, page):
    s = qa.sent["payment_id"]
    log_in(page)
    page.goto("/")
    page.wait_for_selector(sel(f"activity-pay-again-{s}"))
    page.click(sel(f"activity-pay-again-{s}"))
    page.wait_for_timeout(500)
    assert "/payment/" not in page.url


def test_from_the_payment_page_the_forms_on_home_are_prefilled(qa, new_page):
    s, r = qa.sent["payment_id"], qa.recv["payment_id"]
    page = new_page()
    log_in(page)
    page.goto(f"/payment/{s}")
    page.wait_for_selector(sel("payment-detail-pay-again"))
    page.wait_for_load_state("networkidle")
    writes = Writes(page)
    page.click(sel("payment-detail-pay-again"))
    page.wait_for_url(re.compile(r"/$"))
    pw_expect(page.locator(sel("pay-handle"))).to_have_value("bob")
    assert val(page, "pay-amount") == "12.50" and val(page, "pay-visibility") == "private"
    pw_expect(page.locator(sel("pay-amount"))).to_be_focused()
    page.goto(f"/payment/{r}")
    page.wait_for_selector(sel("payment-detail-request-again"))
    page.wait_for_load_state("networkidle")
    page.click(sel("payment-detail-request-again"))
    pw_expect(page.locator(sel("request-handle"))).to_have_value("cy")
    assert writes.items == []


def test_prefill_does_not_linger_after_a_reload(qa, page):
    s = qa.sent["payment_id"]
    log_in(page)
    page.goto("/")
    page.wait_for_selector(sel(f"activity-pay-again-{s}"))
    page.click(sel(f"activity-pay-again-{s}"))
    pw_expect(page.locator(sel("pay-handle"))).to_have_value("bob")
    page.reload()
    page.wait_for_selector(sel("pay-handle"))
    page.wait_for_load_state("networkidle")
    assert val(page, "pay-handle") == "" and val(page, "pay-amount") == ""


def test_jpy_amounts_prefill_without_a_decimal_point(reset, api, page):
    reset(m.fixture([m.ADA, m.BOB], currency="JPY"))
    p = expect(api().authenticate("ada@example.com").pay("bob", 1_500), 201).json()
    log_in(page)
    page.goto("/")
    page.wait_for_selector(sel(f"activity-pay-again-{p['payment_id']}"))
    page.click(sel(f"activity-pay-again-{p['payment_id']}"))
    pw_expect(page.locator(sel("pay-amount"))).to_have_value("1500")


def test_prefill_at_375_reveals_the_form_without_further_clicks(qa, new_page):
    s, r = qa.sent["payment_id"], qa.recv["payment_id"]
    page = new_page(viewport={"width": 375, "height": 740})
    log_in(page)
    page.goto("/")
    page.wait_for_selector(sel(f"activity-request-again-{r}"))
    page.wait_for_load_state("networkidle")
    page.click(sel(f"activity-request-again-{r}"))
    pw_expect(page.locator(sel("request-handle"))).to_be_visible()
    pw_expect(page.locator(sel("request-handle"))).to_have_value("cy")
    pw_expect(page.locator(sel("request-amount"))).to_be_focused()
    page.click(sel(f"activity-pay-again-{s}"))
    pw_expect(page.locator(sel("pay-handle"))).to_be_visible()
    pw_expect(page.locator(sel("pay-handle"))).to_have_value("bob")
    d = page.evaluate("() => { const e = document.scrollingElement; return [e.scrollWidth, e.clientWidth]; }")
    assert d[0] <= d[1] + 1, d


def test_the_buttons_are_keyboard_operable_and_named(qa, page):
    s = qa.sent["payment_id"]
    log_in(page)
    page.goto("/")
    page.wait_for_selector(sel(f"activity-pay-again-{s}"))
    page.wait_for_load_state("networkidle")
    name = page.evaluate("""(tid) => { const e = document.querySelector(`[data-testid='${tid}']`);
        return (e.getAttribute('aria-label') || e.textContent || '').trim(); }""", f"activity-pay-again-{s}")
    assert re.search(r"again", name, re.I), name
    page.focus(sel(f"activity-pay-again-{s}"))
    page.keyboard.press("Enter")
    pw_expect(page.locator(sel("pay-handle"))).to_have_value("bob")


def test_buttons_make_no_stray_text_and_pass_contrast(qa, new_page):
    for scheme in ("light", "dark"):
        page = new_page(color_scheme=scheme)
        log_in(page)
        page.goto("/")
        page.wait_for_selector(sel(f"activity-pay-again-{qa.sent['payment_id']}"))
        settle(page, 300)
        assert _stray(page) == []
        res = page.evaluate(_SCAN_JS)
        assert not res["bad"], f"{scheme}: {res['bad'][:6]}"


def test_pay_again_click_is_never_lost_after_load(qa, page):
    s = qa.sent["payment_id"]
    log_in(page)
    lost = 0
    for _ in range(15):
        page.goto("/")
        page.wait_for_selector(sel(f"activity-pay-again-{s}"))
        page.click(sel(f"activity-pay-again-{s}"))
        try:
            pw_expect(page.locator(sel("pay-handle"))).to_have_value("bob", timeout=2_000)
        except AssertionError:
            lost += 1
    assert lost == 0, f"{lost} of 15 clicks did nothing"


# ---- split explanation -----------------------------------------------------------------------------------

def _split_note(page, amount, handles):
    page.goto("/split")
    page.wait_for_selector(sel("split-amount"))
    page.wait_for_load_state("networkidle")
    page.fill(sel("split-amount"), amount)
    page.fill(sel("split-handles"), handles)
    page.wait_for_selector(sel("split-extra-note"))
    return (page.text_content(sel("split-extra-note")) or "").strip()


@pytest.fixture
def sp(reset, page):
    reset(m.fixture([m.ADA, m.BOB, m.CY, m.user("dee", 0, display_name="Dee")]))
    log_in(page)
    return page


@pytest.mark.parametrize("amount,handles", [("10.00", "ada,bob,cy"), ("0.10", "ada,bob,cy"),
                                            ("0.01", "ada,bob,cy"), ("0.07", "ada,bob,cy,dee"),
                                            ("0.05", "dee,cy,bob,ada"), ("0.11", "cy,ada,bob"),
                                            ("9.99", "ada,bob,cy"), ("0.05", "ada,bob,cy,dee,ada")])
def test_the_note_names_exactly_the_people_who_carry_the_extra_unit(sp, amount, handles):
    note = _split_note(sp, amount, handles)
    minor = round(float(amount) * 100)
    order = []
    for h in handles.split(","):
        if h not in order:
            order.append(h)
    shares = m.equal_split(minor, len(handles.split(",")))      # the server splits over the list as given
    base = min(shares)
    for h, sh in zip(handles.split(","), shares):
        text = (sp.text_content(sel(f"split-share-{h}")) or "").strip()
        if len(order) == len(handles.split(",")):
            assert text == m.money(sh), (h, text)
    carriers = [h for h, sh in zip(handles.split(","), shares) if sh > base] if len(set(shares)) > 1 else []
    if len(order) == len(handles.split(",")):
        for h in order:
            mentioned = re.search(rf"@{h}\b", note) is not None
            assert mentioned == (h in carriers), f"{h}: carriers {carriers}; note: {note}"
    if not carriers:
        assert re.search(r"even|same|equal", note, re.I), note
    else:
        assert re.search(r"extra|leftover|remaind|one more", note, re.I), note
        assert m.money(1) in note, f"the extra unit should be named as {m.money(1)}: {note}"
    assert not re.search(r"undefined|NaN|null", note), note


def test_the_explanation_describes_list_order_not_alphabetical(sp):
    note = _split_note(sp, "0.10", "cy,bob,ada")                  # extra goes to cy, the first listed
    assert "@cy" in note and "@bob" not in note and "@ada" not in note
    note2 = _split_note(sp, "0.10", "ada,bob,cy")
    assert "@ada" in note2 and "@cy" not in note2
    assert re.search(r"order|first|list", note2, re.I), note2


def test_the_note_follows_the_inputs_live_and_shares_stay_exact(sp):
    sp.goto("/split")
    sp.wait_for_selector(sel("split-amount"))
    sp.wait_for_load_state("networkidle")
    sp.fill(sel("split-handles"), "ada,bob,cy")
    sp.fill(sel("split-amount"), "10.00")
    pw_expect(sp.locator(sel("split-share-ada"))).to_have_text("3.34 EUR")
    pw_expect(sp.locator(sel("split-share-bob"))).to_have_text("3.33 EUR")
    pw_expect(sp.locator(sel("split-extra-note"))).to_contain_text("@ada")
    sp.fill(sel("split-handles"), "bob,ada,cy")
    pw_expect(sp.locator(sel("split-share-bob"))).to_have_text("3.34 EUR")
    pw_expect(sp.locator(sel("split-extra-note"))).to_contain_text("@bob")
    pw_expect(sp.locator(sel("split-extra-note"))).not_to_contain_text("@ada")
    sp.fill(sel("split-amount"), "9.99")
    pw_expect(sp.locator(sel("split-share-ada"))).to_have_text("3.33 EUR")
    assert re.search(r"even|same|equal", sp.text_content(sel("split-extra-note")), re.I)


def test_the_submitted_split_matches_the_explained_shares(sp):
    sp.goto("/split")
    sp.wait_for_selector(sel("split-amount"))
    sp.wait_for_load_state("networkidle")
    sp.fill(sel("split-handles"), "bob,cy,dee")
    sp.fill(sel("split-amount"), "0.10")
    note = sp.text_content(sel("split-extra-note"))
    assert "@bob" in note and "@cy" not in note
    shown = {h: sp.text_content(sel(f"split-share-{h}")).strip() for h in ("bob", "cy", "dee")}
    assert shown == {"bob": "0.04 EUR", "cy": "0.03 EUR", "dee": "0.03 EUR"}
    sp.click(sel("split-submit"))
    sp.wait_for_timeout(800)
    assert not sp.locator(sel("split-error")).count()


def test_split_layout_and_contrast_with_the_note(sp, new_page):
    for scheme, width in (("light", 375), ("dark", 375), ("light", 1280), ("dark", 1280)):
        page = new_page(color_scheme=scheme, viewport={"width": width, "height": 800})
        log_in(page)
        _split_note(page, "0.10", "ada,bob,cy")
        settle(page, 200)
        d = page.evaluate("() => { const e = document.scrollingElement; return [e.scrollWidth, e.clientWidth]; }")
        assert d[0] <= d[1] + 1, (scheme, width, d)
        res = page.evaluate(_SCAN_JS)
        assert not res["bad"], f"{scheme}: {res['bad'][:6]}"
        assert _stray(page) == []
