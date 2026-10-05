"""S2-U9 the /split screen: preview by the §9 rule before posting, identical to the server,
handle parsing, refusals, and one split per unchanged submission."""
from __future__ import annotations

import pytest
from playwright.sync_api import expect as pw_expect

import pf_model as m
from ui_kit import Writes, absent, log_in, sel, settle

USERS = [m.ADA, m.BOB, m.CY, m.user("dee", 0), m.user("eve", 0)]


@pytest.fixture
def seeded(reset):
    reset(m.fixture(USERS))


def _preview(page, amount, handles):
    page.goto("/split")
    page.fill(sel("split-amount"), amount)
    page.fill(sel("split-handles"), handles)
    page.wait_for_selector(sel("split-preview"))


@pytest.mark.parametrize("amount,handles,minor", [
    ("10.00", ["ada", "bob", "cy"], 1_000), ("0.01", ["ada", "bob", "cy"], 1),
    ("0.10", ["ada", "bob", "cy"], 10), ("9.99", ["ada", "bob", "cy"], 999),
    ("0.05", ["ada", "bob", "cy", "dee", "eve"], 5), ("10", ["cy", "bob", "ada"], 1_000),
    ("100.01", ["bob", "dee"], 10_001), ("7", ["eve"], 700),
])
def test_preview_follows_the_split_rule_before_anything_is_posted(seeded, page, amount,
                                                                   handles, minor):
    log_in(page)
    writes = Writes(page)
    _preview(page, amount, ",".join(handles))
    want = m.equal_split(minor, len(handles))
    for h, share in zip(handles, want):
        pw_expect(page.locator(sel(f"split-share-{h}"))).to_have_text(m.money(share))
    assert page.locator(f"{sel('split-preview')} [data-testid^='split-share-']").count() == \
        len(handles)
    settle(page, 200)
    assert writes.money_writes() == [], "the preview must not post anything"


@pytest.mark.parametrize("raw,handles", [(" ada , bob,cy ", ["ada", "bob", "cy"]),
                                         ("ada,,bob,", ["ada", "bob"]),
                                         ("@ada, @bob", ["ada", "bob"])])
def test_handle_parsing(seeded, page, raw, handles):
    """[S2-U9] trimmed, empties dropped, one leading @ stripped."""
    log_in(page)
    _preview(page, "1.00", raw)
    want = m.equal_split(100, len(handles))
    for h, share in zip(handles, want):
        pw_expect(page.locator(sel(f"split-share-{h}"))).to_have_text(m.money(share))


def test_preview_updates_as_inputs_change(seeded, page):
    log_in(page)
    _preview(page, "10.00", "ada,bob,cy")
    pw_expect(page.locator(sel("split-share-ada"))).to_have_text("3.34 EUR")
    page.fill(sel("split-handles"), "bob,ada")
    pw_expect(page.locator(sel("split-share-bob"))).to_have_text("5.00 EUR")
    pw_expect(page.locator(sel("split-share-cy"))).to_have_count(0)
    page.fill(sel("split-amount"), "0.03")
    pw_expect(page.locator(sel("split-share-ada"))).to_have_text("0.01 EUR")


def test_submitted_split_has_the_previewed_shares(seeded, page, api):
    log_in(page)
    _preview(page, "10.00", "cy,ada,bob")
    shown = {h: page.text_content(sel(f"split-share-{h}")).strip() for h in ("cy", "ada", "bob")}
    page.fill(sel("split-note"), "dinner")
    page.click(sel("split-submit"))
    bob = api().authenticate("bob@example.com")
    cy = api().authenticate("cy@example.com")
    for _ in range(100):
        if bob.requests_list() and cy.requests_list():
            break
        page.wait_for_timeout(100)
    got = {"bob": bob.requests_list()[0]["amount"], "cy": cy.requests_list()[0]["amount"]}
    assert got == {"cy": 334, "bob": 333}
    assert shown == {"cy": "3.34 EUR", "ada": "3.33 EUR", "bob": "3.33 EUR"}
    assert bob.requests_list()[0]["note"] == "dinner"
    absent(page, "split-error")


def test_unchanged_resubmission_creates_one_split(seeded, page, api):
    log_in(page)
    _preview(page, "3.00", "ada,bob")
    page.click(sel("split-submit"))
    bob = api().authenticate("bob@example.com")
    for _ in range(100):
        if bob.requests_list():
            break
        page.wait_for_timeout(100)
    page.click(sel("split-submit"))
    settle(page)
    assert len(bob.requests_list()) == 1
    absent(page, "split-error")


@pytest.mark.parametrize("amount,handles", [("10.00", "ada,nobody"), ("10.00", "ada,bob,ada"),
                                            ("10.001", "ada,bob"), ("ten", "ada,bob"),
                                            ("10.00", "")])
def test_refusals_show_split_error(seeded, page, api, amount, handles):
    log_in(page)
    page.goto("/split")
    page.fill(sel("split-amount"), amount)
    page.fill(sel("split-handles"), handles)
    page.click(sel("split-submit"))
    page.wait_for_selector(sel("split-error"))
    assert api().authenticate("bob@example.com").requests_list() == []


def test_split_with_only_the_caller_is_valid(seeded, page, api):
    log_in(page)
    _preview(page, "5.00", "ada")
    pw_expect(page.locator(sel("split-share-ada"))).to_have_text("5.00 EUR")
    page.click(sel("split-submit"))
    settle(page)
    absent(page, "split-error")
