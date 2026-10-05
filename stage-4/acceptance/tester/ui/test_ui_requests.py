"""S2-U8 the /requests screen: lists, statuses, buttons only where allowed, pay/decline/
cancel, refusals and stale buttons, empty state, refresh after every action."""
from __future__ import annotations

import pytest
from playwright.sync_api import expect as pw_expect

import pf_model as m
from pf_client import expect
from ui_kit import absent, log_in, sel, settle, text, wait_wallet


@pytest.fixture
def seeded(reset):
    reset(m.fixture())


def _client(api, handle):
    return api().authenticate(f"{handle}@example.com")


def _status(page, rid):
    return page.get_attribute(sel(f"request-item-{rid}"), "data-status")


def test_lists_place_each_request_by_direction(seeded, page, api):
    inc = expect(_client(api, "bob").ask("ada", 1_200, note="taxi"), 201).json()["request_id"]
    out = expect(_client(api, "ada").ask("cy", 300), 201).json()["request_id"]
    other = expect(_client(api, "bob").ask("cy", 5), 201).json()["request_id"]
    log_in(page)
    page.goto("/requests")
    page.wait_for_selector(f"{sel('incoming-list')} {sel('request-item-' + inc)}")
    page.wait_for_selector(f"{sel('outgoing-list')} {sel('request-item-' + out)}")
    assert page.locator(sel(f"request-item-{other}")).count() == 0
    assert text(page, f"request-amount-{inc}").strip() == "12.00 EUR"
    assert text(page, f"request-amount-{out}").strip() == "3.00 EUR"
    assert _status(page, inc) == "pending" and _status(page, out) == "pending"
    for tid in (f"request-pay-{inc}", f"request-decline-{inc}", f"request-cancel-{out}"):
        pw_expect(page.locator(sel(tid))).to_be_visible()
    for tid in (f"request-cancel-{inc}", f"request-pay-{out}", f"request-decline-{out}"):
        assert page.locator(sel(tid)).count() == 0, tid
    absent(page, "empty-requests")


def test_closed_requests_show_their_status_and_no_buttons(reset, page):
    rq = lambda rid, a, b, s: {"id": rid, "requester_id": f"u_{a}", "payer_id": f"u_{b}",
                               "amount": 100, "note": "", "status": s}
    reset(m.fixture(requests=[rq("rq_p", "bob", "ada", "paid"),
                              rq("rq_d", "bob", "ada", "declined"),
                              rq("rq_c", "ada", "bob", "cancelled"),
                              rq("rq_o", "ada", "cy", "pending")]))
    log_in(page)
    page.goto("/requests")
    page.wait_for_selector(sel("request-item-rq_o"))
    for rid, s in (("rq_p", "paid"), ("rq_d", "declined"), ("rq_c", "cancelled"),
                   ("rq_o", "pending")):
        assert _status(page, rid) == s
    for rid in ("rq_p", "rq_d", "rq_c"):
        for b in ("pay", "decline", "cancel"):
            assert page.locator(sel(f"request-{b}-{rid}")).count() == 0, (b, rid)


def test_paying_an_incoming_request(seeded, page, api):
    rid = expect(_client(api, "bob").ask("ada", 1_200), 201).json()["request_id"]
    log_in(page)
    page.goto("/requests")
    page.click(sel(f"request-pay-{rid}"))
    page.wait_for_selector(f"{sel('request-item-' + rid)}[data-status='paid']")
    for b in ("pay", "decline"):
        assert page.locator(sel(f"request-{b}-{rid}")).count() == 0
    assert _client(api, "ada").balance() == 8_800
    page.goto("/")
    wait_wallet(page, 8_800)


def test_double_click_on_pay_moves_money_once(seeded, page, api):
    rid = expect(_client(api, "bob").ask("ada", 700), 201).json()["request_id"]
    log_in(page)
    page.goto("/requests")
    page.dblclick(sel(f"request-pay-{rid}"))
    page.wait_for_selector(f"{sel('request-item-' + rid)}[data-status='paid']")
    settle(page)
    assert _client(api, "ada").balance() == 9_300
    absent(page, "request-error")


def test_declining_and_cancelling(seeded, page, api):
    inc = expect(_client(api, "bob").ask("ada", 100), 201).json()["request_id"]
    out = expect(_client(api, "ada").ask("bob", 100), 201).json()["request_id"]
    log_in(page)
    page.goto("/requests")
    page.click(sel(f"request-decline-{inc}"))
    page.wait_for_selector(f"{sel('request-item-' + inc)}[data-status='declined']")
    page.click(sel(f"request-cancel-{out}"))
    page.wait_for_selector(f"{sel('request-item-' + out)}[data-status='cancelled']")
    assert page.locator(sel(f"request-cancel-{out}")).count() == 0
    assert _client(api, "ada").balance() == 10_000


def test_paying_without_available_funds_shows_request_error(seeded, page, api):
    rid = expect(_client(api, "ada").ask("cy", 90_000), 201).json()["request_id"]
    log_in(page, email="cy@example.com")
    page.goto("/requests")
    page.click(sel(f"request-pay-{rid}"))
    page.wait_for_selector(sel("request-error"))
    assert _status(page, rid) == "pending"
    pw_expect(page.locator(sel(f"request-pay-{rid}"))).to_be_visible()


def test_held_funds_cannot_pay_a_request(reset, page, api):
    reset(m.fixture(authorizations=[m.hold("a_1", "cy", "ada", 450)]))
    rid = expect(_client(api, "bob").ask("cy", 100), 201).json()["request_id"]
    log_in(page, email="cy@example.com")
    page.goto("/requests")
    page.click(sel(f"request-pay-{rid}"))
    page.wait_for_selector(sel("request-error"))
    assert _client(api, "cy").balance() == 500


def test_request_cancelled_elsewhere_shows_error_and_the_stale_button_goes(seeded, page, api):
    """[S2 competing clients] refused pay -> request-error and the list refreshes."""
    bob = _client(api, "bob")
    rid = expect(bob.ask("ada", 100), 201).json()["request_id"]
    log_in(page)
    page.goto("/requests")
    pw_expect(page.locator(sel(f"request-pay-{rid}"))).to_be_visible()
    expect(bob.post(f"/requests/{rid}/cancel", json={}), 200)
    page.click(sel(f"request-pay-{rid}"))
    page.wait_for_selector(sel("request-error"))
    pw_expect(page.locator(sel(f"request-pay-{rid}"))).to_have_count(0)
    pw_expect(page.locator(sel(f"request-item-{rid}"))).to_have_attribute("data-status",
                                                                        "cancelled")
    assert _client(api, "ada").balance() == 10_000


def test_cancel_after_paid_elsewhere_shows_error(seeded, page, api):
    rid = expect(_client(api, "ada").ask("bob", 100), 201).json()["request_id"]
    log_in(page)
    page.goto("/requests")
    pw_expect(page.locator(sel(f"request-cancel-{rid}"))).to_be_visible()
    expect(_client(api, "bob").pay_request(rid, {}), 201)
    page.click(sel(f"request-cancel-{rid}"))
    page.wait_for_selector(sel("request-error"))
    pw_expect(page.locator(sel(f"request-item-{rid}"))).to_have_attribute("data-status", "paid")
    pw_expect(page.locator(sel(f"request-cancel-{rid}"))).to_have_count(0)


def test_decline_after_cancelled_elsewhere_shows_error(seeded, page, api):
    bob = _client(api, "bob")
    rid = expect(bob.ask("ada", 100), 201).json()["request_id"]
    log_in(page)
    page.goto("/requests")
    pw_expect(page.locator(sel(f"request-decline-{rid}"))).to_be_visible()
    expect(bob.post(f"/requests/{rid}/cancel", json={}), 200)
    page.click(sel(f"request-decline-{rid}"))
    page.wait_for_selector(sel("request-error"))
    pw_expect(page.locator(sel(f"request-decline-{rid}"))).to_have_count(0)


def test_empty_requests(seeded, page):
    log_in(page)
    page.goto("/requests")
    page.wait_for_selector(sel("empty-requests"))
    page.wait_for_selector(sel("incoming-list"), state="attached")
    page.wait_for_selector(sel("outgoing-list"), state="attached")


def test_empty_state_goes_away_when_a_request_arrives_and_refresh_shows_it(seeded, page, api):
    log_in(page)
    page.goto("/requests")
    page.wait_for_selector(sel("empty-requests"))
    rid = expect(_client(api, "bob").ask("ada", 5), 201).json()["request_id"]
    page.goto("/requests")
    page.wait_for_selector(sel(f"request-item-{rid}"))
    absent(page, "empty-requests")


def test_split_shares_of_zero_are_payable_from_the_screen(seeded, page, api):
    s = expect(_client(api, "ada").split(1, ["ada", "bob", "cy"]), 201).json()
    zero = next(r for r in s["requests"] if r["payer_handle"] == "cy")
    assert zero["amount"] == 0
    log_in(page, email="cy@example.com")
    page.goto("/requests")
    assert text(page, f"request-amount-{zero['request_id']}").strip() == "0.00 EUR"
    page.click(sel(f"request-pay-{zero['request_id']}"))
    page.wait_for_selector(f"{sel('request-item-' + zero['request_id'])}[data-status='paid']")
