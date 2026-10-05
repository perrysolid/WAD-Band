"""S2-U7 the activity feed on `/`: parts, exact text, order, visibility, empty state,
and human-first formatting (no raw timestamps or user ids)."""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect as pw_expect

import pf_model as m
from pf_client import expect
from ui_kit import fill_pay, log_in, sel, text, wait_wallet


@pytest.fixture
def seeded(reset):
    reset(m.fixture())


def _client(api, handle):
    return api().authenticate(f"{handle}@example.com")


def test_feed_item_parts(seeded, page, api):
    ada = _client(api, "ada")
    p = expect(ada.pay("bob", 2_500, note="dinner", visibility="private"), 201).json()
    log_in(page)
    page.goto("/")
    pid = p["payment_id"]
    page.wait_for_selector(sel(f"activity-item-{pid}"))
    assert page.get_attribute(sel(f"activity-item-{pid}"), "data-visibility") == "private"
    parties = text(page, f"activity-parties-{pid}")
    assert "ada" in parties and "bob" in parties
    assert text(page, f"activity-amount-{pid}").strip() == "25.00 EUR"
    assert text(page, f"activity-note-{pid}") == "dinner"


def test_note_is_rendered_as_exact_text_never_markup(seeded, page, api):
    note = '<img src=x onerror="window.__xss=1"><b>bold</b> café \U0001F37D & "q"'
    p = expect(_client(api, "ada").pay("bob", 1, note=note), 201).json()
    log_in(page)
    page.goto("/")
    page.wait_for_selector(sel(f"activity-note-{p['payment_id']}"))
    assert text(page, f"activity-note-{p['payment_id']}") == note
    assert page.evaluate("() => window.__xss") is None
    assert page.locator(f"{sel('activity-list')} img[src='x']").count() == 0


def test_empty_note_element_is_present_and_empty(seeded, page, api):
    p = expect(_client(api, "ada").pay("bob", 1), 201).json()
    log_in(page)
    page.goto("/")
    page.wait_for_selector(sel(f"activity-note-{p['payment_id']}"), state="attached")
    assert text(page, f"activity-note-{p['payment_id']}") == ""


def test_newest_first_in_the_dom(seeded, page, api):
    ada = _client(api, "ada")
    made = [expect(ada.pay("bob", 10 + i), 201).json() for i in range(4)]
    made.append(expect(_client(api, "cy").pay("ada", 7), 201).json())
    log_in(page)
    page.goto("/")
    page.wait_for_selector(sel(f"activity-item-{made[-1]['payment_id']}"))
    order = page.eval_on_selector_all(f"{sel('activity-list')} > *",
                                      "els => els.map(e => e.getAttribute('data-testid'))")
    rendered = [o for o in order if o and o.startswith("activity-item-")]
    assert rendered == [f"activity-item-{p['payment_id']}" for p in reversed(made)]


def test_feed_contract_for_a_third_party(seeded, page, api):
    pub = expect(_client(api, "bob").pay("cy", 100, visibility="public"), 201).json()
    prv = expect(_client(api, "bob").pay("cy", 200, visibility="private"), 201).json()
    log_in(page)
    page.goto("/")
    page.wait_for_selector(sel(f"activity-item-{pub['payment_id']}"))
    assert page.locator(sel(f"activity-item-{prv['payment_id']}")).count() == 0
    assert page.get_attribute(sel(f"activity-item-{pub['payment_id']}"),
                              "data-visibility") == "public"


def test_private_payment_is_visible_to_its_receiver(seeded, page, api):
    prv = expect(_client(api, "ada").pay("cy", 200, visibility="private"), 201).json()
    log_in(page, email="cy@example.com")
    page.goto("/")
    page.wait_for_selector(sel(f"activity-item-{prv['payment_id']}"))
    assert page.get_attribute(sel(f"activity-item-{prv['payment_id']}"),
                              "data-visibility") == "private"


def test_empty_activity_replaces_the_list(seeded, page):
    log_in(page, email="cy@example.com")
    page.goto("/")
    page.wait_for_selector(sel("empty-activity"))
    assert page.locator(sel("activity-list")).count() == 0 or \
        page.locator(f"{sel('activity-list')} [data-testid^='activity-item-']").count() == 0


def test_own_payment_appears_without_a_reload(seeded, page):
    log_in(page, email="cy@example.com")
    page.goto("/")
    page.wait_for_selector(sel("empty-activity"))
    fill_pay(page, handle="bob", amount="1.50", note="first")
    page.click(sel("pay-submit"))
    page.wait_for_selector(f"{sel('activity-list')} [data-testid^='activity-item-']")
    pw_expect(page.locator(sel("empty-activity"))).to_have_count(0)
    wait_wallet(page, 350)


def test_captures_and_request_payments_appear_in_the_feed(seeded, page, api):
    ada, bob = _client(api, "ada"), _client(api, "bob")
    a = expect(ada.authorize("bob", 900, note="deposit"), 201).json()
    cap = expect(bob.capture(a["authorization_id"], {"amount": 400}), 201).json()
    rid = expect(bob.ask("ada", 30), 201).json()["request_id"]
    via = expect(ada.pay_request(rid, {}), 201).json()
    log_in(page)
    page.goto("/")
    for p in (cap, via):
        page.wait_for_selector(sel(f"activity-item-{p['payment_id']}"))
    assert text(page, f"activity-amount-{cap['payment_id']}").strip() == "4.00 EUR"
    assert text(page, f"activity-note-{cap['payment_id']}") == "deposit"
    assert page.locator(f"{sel('activity-list')} [data-testid^='activity-item-']").count() == 2


def test_items_are_formatted_for_people(seeded, page, api):
    """[S2 visual direction] no raw RFC 3339 stamps or user ids in the feed."""
    p = expect(_client(api, "ada").pay("bob", 1_234, note="x"), 201).json()
    log_in(page)
    page.goto("/")
    item = page.locator(sel(f"activity-item-{p['payment_id']}"))
    item.wait_for()
    shown = item.inner_text()
    assert p["created_at"] not in shown
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", shown), shown
    assert "u_ada" not in shown and "u_bob" not in shown


def test_feed_shows_more_than_one_page_of_history_newest_first(reset, page, api):
    reset(m.fixture([m.user("ada", 100_000), m.BOB]))
    ada = _client(api, "ada")
    made = [expect(ada.pay("bob", 1 + i), 201).json()["payment_id"] for i in range(12)]
    log_in(page)
    page.goto("/")
    page.wait_for_selector(sel(f"activity-item-{made[-1]}"))
    order = page.eval_on_selector_all(f"{sel('activity-list')} [data-testid^='activity-item-']",
                                      "els => els.map(e => e.getAttribute('data-testid'))")
    assert order[:12] == [f"activity-item-{pid}" for pid in reversed(made)]
