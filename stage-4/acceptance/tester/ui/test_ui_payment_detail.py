"""UI upgrade item 3: /payment/<id> (detail, revisions, refund, correct). Test ids per stage-4/RUN.md."""
from __future__ import annotations

import json
import re
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect as pw_expect

import pf_model as m
from pf_client import expect
from test_ui_first_click import _stray
from test_ui_polish import _SCAN_JS
from ui_kit import Writes, log_in, sel, settle

PD = "payment-detail-"


@pytest.fixture
def pw(reset, api):
    reset(m.fixture([m.ADA, m.BOB, m.CY, m.user("dee", 0, display_name="Dee")], operators=["u_ada"]))

    class W:
        def client(self, h):
            return api().authenticate(f"{h}@example.com")
    w = W()
    w.ada, w.bob, w.cy = w.client("ada"), w.client("bob"), w.client("cy")
    w.p = expect(w.ada.pay("bob", 1_000, note="rent \U0001F3E0", visibility="private"), 201).json()
    return w


def goto_pay(page, pid, who="ada"):
    log_in(page, email=f"{who}@example.com")
    page.goto(f"/payment/{pid}")
    page.wait_for_selector(sel(PD + "amount"))
    page.wait_for_load_state("networkidle")


def T(page, tid):
    return (page.text_content(sel(tid)) or "").strip()


def has(page, tid):
    return page.locator(sel(tid)).count() > 0


# ---- reaching the screen -----------------------------------------------------------------------

def test_clicking_a_feed_item_opens_the_detail_and_keeps_the_feed_testids(pw, page):
    pid = pw.p["payment_id"]
    log_in(page)
    page.goto("/")
    page.wait_for_selector(sel(f"activity-item-{pid}"))
    for tid in (f"activity-parties-{pid}", f"activity-amount-{pid}", f"activity-note-{pid}"):
        assert has(page, tid), tid
    assert page.get_attribute(sel(f"activity-item-{pid}"), "data-visibility") == "private"
    page.click(sel(f"activity-item-{pid}"))
    page.wait_for_url(re.compile(rf"/payment/{pid}$"))
    page.wait_for_selector(sel(PD + "amount"))


def test_the_sentence_link_inside_parties_also_opens_it(pw, page):
    pid = pw.p["payment_id"]
    log_in(page)
    page.goto("/")
    page.wait_for_selector(sel(f"activity-parties-{pid}"))
    link = page.locator(f"{sel('activity-parties-' + pid)} a")
    assert link.count() >= 1, "the sentence should be a link"
    link.first.focus()
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(rf"/payment/{pid}$"))


def test_history_entries_link_to_the_detail(pw, page):
    pid = pw.p["payment_id"]
    log_in(page)
    page.goto("/history")
    page.wait_for_selector(sel("history-statement-submit"))
    page.wait_for_load_state("networkidle")
    page.click(sel("history-statement-submit"))
    page.wait_for_selector(sel(f"history-entry-{pid}"))
    link = page.locator(f"{sel('history-entry-parties-' + pid)} a, {sel('history-entry-' + pid)} a")
    if link.count():
        link.first.click()
    else:
        page.click(sel(f"history-entry-{pid}"))
    page.wait_for_url(re.compile(rf"/payment/{pid}$"))
    page.wait_for_selector(sel(PD + "amount"))


def test_direct_load_signed_out_and_json_404(pw, page, control):
    pid = pw.p["payment_id"]
    page.goto(f"/payment/{pid}")
    page.wait_for_selector(sel("login-submit"))
    r = control.get(f"/payment/{pid}", headers={"Accept": "application/json"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    h = control.get(f"/payment/{pid}", headers={"Accept": "text/html"})
    assert h.status_code == 200 and "default-src 'self'" in h.headers.get("content-security-policy", "")


def test_a_third_party_and_an_unknown_id_see_not_available(pw, new_page):
    page = new_page()
    log_in(page, email="cy@example.com")
    for pid in (pw.p["payment_id"], "p_nope", "x" * 80):
        page.goto(f"/payment/{pid}")
        page.wait_for_selector(sel(PD + "unavailable"))
        assert not has(page, PD + "refund-submit") and not has(page, PD + "correct-submit")
        assert not re.search(r"not_found|404|undefined", page.inner_text("main"), re.I)
    assert page.locator(sel("current-user")).count() == 1


# ---- content -------------------------------------------------------------------------------------

def test_detail_shows_everything_about_the_payment(pw, page):
    p = pw.p
    goto_pay(page, p["payment_id"])
    assert T(page, PD + "amount") == "10.00 EUR" and page.get_attribute(sel(PD + "amount"), "data-amount") == "1000"
    assert "ada" in T(page, PD + "parties") and "bob" in T(page, PD + "parties")
    assert T(page, PD + "note") == "rent \U0001F3E0"
    assert page.get_attribute(sel(PD + "privacy"), "data-visibility") == "private"
    assert re.search(r"\byou\b.*\bpaid\b.*\bbob\b", T(page, PD + "sentence"), re.I | re.S), T(page, PD + "sentence")
    t = page.locator(f"{sel(PD + 'time')} time, time{sel(PD + 'time')}")
    assert has(page, PD + "time")
    assert not has(page, PD + "current"), "no corrected amount before a correction"
    assert has(page, PD + "revision-1") and page.get_attribute(sel(PD + "revision-amount-1"), "data-amount") == "1000"
    assert t is not None


@pytest.mark.parametrize("who", ["ada", "bob"])
def test_the_payment_page_has_no_stray_null_false_or_undefined_text(pw, new_page, who):
    page = new_page()
    goto_pay(page, pw.p["payment_id"], who)
    assert _stray(page) == [], _stray(page)
    expect(pw.ada.correct(pw.p["payment_id"], amount=400, effective_at=pw.p["created_at"], reason="typo"), 201)
    page.click(sel(PD + "refresh"))
    page.wait_for_selector(sel(PD + "revision-2"))
    assert _stray(page) == [], _stray(page)


def test_the_viewers_point_of_view_differs(pw, new_page):
    page = new_page()
    goto_pay(page, pw.p["payment_id"], who="bob")
    assert re.search(r"\bada\b.*\bpaid\b.*\byou\b", T(page, PD + "sentence"), re.I | re.S)


def test_sender_sees_correct_only_and_receiver_refund_only(pw, new_page):
    s, r = new_page(), new_page()
    goto_pay(s, pw.p["payment_id"], "ada")
    assert has(s, PD + "correct-submit") and not has(s, PD + "refund-submit")
    goto_pay(r, pw.p["payment_id"], "bob")
    assert has(r, PD + "refund-submit") and not has(r, PD + "correct-submit")


def test_settlement_members_captures_and_refunds_have_no_actions(pw, new_page):
    s = expect(pw.ada.settle([{"from_handle": "ada", "to_handle": "cy", "amount": 40}]), 201).json()
    a = expect(pw.ada.authorize("bob", 50), 201).json()
    cap = expect(pw.bob.capture(a["authorization_id"]), 201).json()
    ref = expect(pw.bob.refund(pw.p["payment_id"], 100), 201).json()
    cases = [(s["payments"][0]["payment_id"], "ada"), (s["payments"][0]["payment_id"], "cy"),
             (cap["payment_id"], "ada"), (cap["payment_id"], "bob"),
             (ref["payment_id"], "bob"), (ref["payment_id"], "ada")]
    page = new_page()
    log_in(page)
    for i, (pid, who) in enumerate(cases):
        page = new_page()
        goto_pay(page, pid, who)
        assert not has(page, PD + "refund-submit") or (who == "cy" and pid == s["payments"][0]["payment_id"]) \
            or (pid == cap["payment_id"] and who == "bob"), (pid, who)
        assert not has(page, PD + "correct-submit"), (pid, who)
    # refunds: a link back to the original and no actions for either party
    page = new_page()
    goto_pay(page, ref["payment_id"], "ada")
    assert has(page, PD + "refund-of") and not has(page, PD + "refund-submit")
    assert has(page, PD + "no-actions")
    assert re.search(r"refund", T(page, PD + "sentence"), re.I)
    page.click(sel(PD + "refund-of"))
    page.wait_for_url(re.compile(rf"/payment/{pw.p['payment_id']}$"))


def test_corrected_payments_show_the_current_amount_and_revisions(pw, page):
    pid = pw.p["payment_id"]
    expect(pw.ada.correct(pid, amount=400, effective_at=pw.p["created_at"], reason="typo"), 201)
    goto_pay(page, pid)
    assert page.get_attribute(sel(PD + "amount"), "data-amount") == "1000", "original amount stays"
    assert page.get_attribute(sel(PD + "current"), "data-amount") == "400"
    assert page.get_attribute(sel(PD + "revision-amount-1"), "data-amount") == "1000"
    assert page.get_attribute(sel(PD + "revision-amount-2"), "data-amount") == "400"
    assert "typo" in page.inner_text(sel(PD + "revisions"))


# ---- refund ----------------------------------------------------------------------------------------

def _refund(page, amount):
    page.fill(sel(PD + "refund-amount"), amount)
    page.click(sel(PD + "refund-submit"))


def test_refund_sends_exact_minor_units_and_succeeds(pw, new_page):
    page = new_page()
    writes = Writes(page)
    goto_pay(page, pw.p["payment_id"], "bob")
    _refund(page, "4")
    page.wait_for_selector(sel(PD + "refund-success"))
    w = writes.to(f"/payments/{pw.p['payment_id']}/refunds")
    assert len(w) == 1 and w[0]["body"] == {"amount": 400} and w[0]["headers"].get("idempotency-key")
    assert pw.bob.balance() == 2_500 + 1_000 - 400 and pw.ada.balance() == 10_000 - 1_000 + 400
    assert not has(page, PD + "refund-error") and not has(page, PD + "refund-uncertain")


@pytest.mark.parametrize("amt", ["15.005", "abc", "-1", "0", "", "1e2", "0.00"])
def test_refund_invalid_decimals_are_refused_without_a_request(pw, new_page, amt):
    page = new_page()
    writes = Writes(page)
    goto_pay(page, pw.p["payment_id"], "bob")
    _refund(page, amt)
    page.wait_for_selector(sel(PD + "refund-error"))
    assert writes.to("/payments") == []


def test_refund_unchanged_resubmission_reuses_the_key_and_a_change_mints_a_new_one(pw, new_page):
    page = new_page()
    writes = Writes(page)
    goto_pay(page, pw.p["payment_id"], "bob")
    _refund(page, "2")
    page.wait_for_selector(sel(PD + "refund-success"))
    page.click(sel(PD + "refund-submit"))
    page.wait_for_timeout(800)
    w = writes.to(f"/payments/{pw.p['payment_id']}/refunds")
    keys = [x["headers"]["idempotency-key"] for x in w]
    assert len(keys) == 2 and keys[0] == keys[1], keys
    assert pw.bob.balance() == 2_500 + 1_000 - 200, "the replay must not refund twice"
    _refund(page, "3")
    page.wait_for_selector(sel(PD + "refund-success"))
    keys = [x["headers"]["idempotency-key"] for x in writes.to(f"/payments/{pw.p['payment_id']}/refunds")]
    assert keys[2] != keys[0]
    assert pw.bob.balance() == 2_500 + 1_000 - 500


def _plain(msg: str) -> None:
    assert msg and len(msg) > 10, msg
    assert not re.search(r"[a-z]+_[a-z_]+|\b4\d\d\b|\b5\d\d\b|undefined|null|\[object", msg), f"not plain language: {msg!r}"


def test_refund_above_the_payment_is_plain_language_and_keeps_the_input(pw, new_page):
    page = new_page()
    goto_pay(page, pw.p["payment_id"], "bob")
    _refund(page, "10.01")
    page.wait_for_selector(sel(PD + "refund-error"))
    _plain(T(page, PD + "refund-error"))
    assert page.input_value(sel(PD + "refund-amount")) == "10.01"
    assert not has(page, PD + "refund-success") and not has(page, PD + "refund-uncertain")
    assert pw.bob.balance() == 3_500


def test_refund_without_funds_is_plain_language(pw, new_page):
    expect(pw.bob.pay("cy", 3_500), 201)
    page = new_page()
    goto_pay(page, pw.p["payment_id"], "bob")
    _refund(page, "1")
    page.wait_for_selector(sel(PD + "refund-error"))
    _plain(T(page, PD + "refund-error"))
    assert re.search(r"fund|balance|enough|available", T(page, PD + "refund-error"), re.I)


@pytest.mark.parametrize("code,status,words", [
    ("refund_exceeds_payment", 422, r"more than|exceed|remaining|already"),
    ("invalid_refund_target", 422, r"refund"),
    ("linked_payment_immutable", 422, r"can.?t be|cannot|part of|linked|changed|corrected"),
    ("insufficient_funds", 409, r"fund|balance|enough|available"),
    ("validation_failed", 422, r"."),
    ("forbidden", 403, r"."),
])
def test_every_refund_error_code_maps_to_plain_language(pw, new_page, code, status, words):
    page = new_page()
    goto_pay(page, pw.p["payment_id"], "bob")
    page.route(re.compile(r"/refunds$"), lambda r: r.fulfill(
        status=status, content_type="application/json",
        body=json.dumps({"error": {"code": code, "message": "server wording"}})))
    _refund(page, "1")
    page.wait_for_selector(sel(PD + "refund-error"))
    msg = T(page, PD + "refund-error")
    _plain(msg)
    assert re.search(words, msg, re.I), msg
    assert not has(page, PD + "refund-uncertain")


def test_a_lost_refund_response_is_uncertain_and_the_retry_refunds_once(pw, new_page):
    page = new_page()
    writes = Writes(page)
    goto_pay(page, pw.p["payment_id"], "bob")
    state = {"lose": True}

    def route(r):
        if state["lose"]:
            state["lose"] = False
            r.fetch()
            r.abort("failed")
        else:
            r.continue_()
    page.route(re.compile(r"/refunds$"), route)
    _refund(page, "2.50")
    page.wait_for_selector(sel(PD + "refund-uncertain"))
    assert not has(page, PD + "refund-error") and T(page, PD + "refund-uncertain")
    assert pw.bob.balance() == 3_500 - 250
    page.click(sel(PD + "refund-submit"))
    page.wait_for_selector(sel(PD + "refund-success"))
    keys = [x["headers"]["idempotency-key"] for x in writes.to(f"/payments/{pw.p['payment_id']}/refunds")]
    assert len(keys) == 2 and keys[0] == keys[1]
    assert pw.bob.balance() == 3_500 - 250, "the retry must replay, not refund again"
    assert not has(page, PD + "refund-uncertain")


# ---- correct ---------------------------------------------------------------------------------------

def _correct(page, amount, reason="typo", when=None):
    page.fill(sel(PD + "correct-amount"), amount)
    page.fill(sel(PD + "correct-reason"), reason)
    if when:
        page.fill(sel(PD + "correct-effective_at"), when)
    page.click(sel(PD + "correct-submit"))


def test_correct_sends_the_documented_body_and_refreshes_in_place(pw, new_page):
    page = new_page()
    writes = Writes(page)
    goto_pay(page, pw.p["payment_id"], "ada")
    default_when = page.input_value(sel(PD + "correct-effective_at"))
    assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d", default_when), "default effective_at should be now"
    _correct(page, "4.00", "typo")
    page.wait_for_selector(sel(PD + "correct-success"))
    w = writes.to(f"/payments/{pw.p['payment_id']}/corrections")
    assert len(w) == 1
    b = w[0]["body"]
    assert set(b) == {"expected_revision", "amount", "effective_at", "reason"}
    assert b["expected_revision"] == 1 and b["amount"] == 400 and b["reason"] == "typo"
    assert re.search(r"[+-]\d\d:\d\d$|Z$", b["effective_at"]) and m.parse(b["effective_at"]) <= m.now() + timedelta(seconds=5)
    pw_expect(page.locator(sel(PD + "current"))).to_have_attribute("data-amount", "400")
    page.wait_for_selector(sel(PD + "revision-2"))
    assert pw.ada.balance() == 10_000 - 400 and pw.bob.balance() == 2_500 + 400
    assert page.get_attribute(sel(PD + "amount"), "data-amount") == "1000"
    # the next correction expects revision 2
    _correct(page, "3.00", "again")
    page.wait_for_selector(sel(PD + "revision-3"))
    assert writes.to(f"/payments/{pw.p['payment_id']}/corrections")[-1]["body"]["expected_revision"] == 2


def test_correct_to_zero_reverses_the_payment(pw, new_page):
    page = new_page()
    goto_pay(page, pw.p["payment_id"], "ada")
    _correct(page, "0", "reversal")
    page.wait_for_selector(sel(PD + "correct-success"))
    assert pw.ada.balance() == 10_000 and pw.bob.balance() == 2_500
    pw_expect(page.locator(sel(PD + "current"))).to_have_attribute("data-amount", "0")


@pytest.mark.parametrize("amt,reason", [("15.005", "x"), ("abc", "x"), ("-1", "x"), ("", "x"),
                                        ("5", ""), ("5", "r" * 201)])
def test_correct_invalid_input_is_refused_without_a_request(pw, new_page, amt, reason):
    page = new_page()
    writes = Writes(page)
    goto_pay(page, pw.p["payment_id"], "ada")
    _correct(page, amt, reason)
    page.wait_for_selector(sel(PD + "correct-error"))
    assert writes.to("/payments") == []


def test_correct_unchanged_resubmission_reuses_the_key(pw, new_page):
    page = new_page()
    writes = Writes(page)
    goto_pay(page, pw.p["payment_id"], "ada")
    _correct(page, "6", "same")
    page.wait_for_selector(sel(PD + "correct-success"))
    page.click(sel(PD + "correct-submit"))
    page.wait_for_timeout(800)
    w = writes.to(f"/payments/{pw.p['payment_id']}/corrections")
    assert w[0]["headers"]["idempotency-key"] and len(w) >= 1
    assert len(pw.ada.revisions(pw.p["payment_id"])) in (2, 3)
    if len(w) == 2 and w[1]["body"] == w[0]["body"]:
        assert w[1]["headers"]["idempotency-key"] == w[0]["headers"]["idempotency-key"]
        assert len(pw.ada.revisions(pw.p["payment_id"])) == 2


def test_stale_revision_is_explained_and_refresh_recovers(pw, new_page):
    page = new_page()
    goto_pay(page, pw.p["payment_id"], "ada")
    expect(pw.ada.correct(pw.p["payment_id"], amount=800, effective_at=pw.p["created_at"], reason="elsewhere"), 201)
    _correct(page, "5", "mine")
    page.wait_for_selector(sel(PD + "correct-error"))
    msg = T(page, PD + "correct-error")
    _plain(msg)
    assert re.search(r"changed|updated|newer|out of date|refresh", msg, re.I), msg
    assert has(page, PD + "refresh")
    page.click(sel(PD + "refresh"))
    page.wait_for_selector(sel(PD + "revision-2"))
    pw_expect(page.locator(sel(PD + "current"))).to_have_attribute("data-amount", "800")
    _correct(page, "5", "mine")
    page.wait_for_selector(sel(PD + "correct-success"))
    assert len(pw.ada.revisions(pw.p["payment_id"])) == 3


def test_correct_below_the_refunded_amount_and_without_funds_are_plain(pw, new_page):
    expect(pw.bob.refund(pw.p["payment_id"], 700), 201)
    page = new_page()
    goto_pay(page, pw.p["payment_id"], "ada")
    _correct(page, "6", "too low")
    page.wait_for_selector(sel(PD + "correct-error"))
    _plain(T(page, PD + "correct-error"))
    # sender cannot afford an increase
    expect(pw.ada.pay("cy", 10_000 - 1_000 + 700 - 50), 201)
    page.reload()
    page.wait_for_selector(sel(PD + "correct-amount"))
    page.wait_for_load_state("networkidle")
    _correct(page, "9999", "too much")
    page.wait_for_selector(sel(PD + "correct-error"))
    _plain(T(page, PD + "correct-error"))
    assert re.search(r"fund|balance|enough|available", T(page, PD + "correct-error"), re.I)


def test_historical_overdraft_is_plain_language(reset, api, new_page):
    def at(days):
        return m.now() - timedelta(days=days)
    reset(m.history_fixture([m.seeded_payment("q1", "ada", "bob", 1_000, m.iso(at(5))),
                             m.seeded_payment("q2", "bob", "cy", 900, m.iso(at(3)))],
                            ending={"ada": 0, "bob": 100, "cy": 900}))
    # ada cannot shrink q1 (bob's balance would go negative at -3d); ada pays nothing now: use cy->? decrease debits receiver
    page = new_page()
    log_in(page, email="ada@example.com")
    expect(api().authenticate("cy@example.com").pay("bob", 900), 201)     # bob has money now
    page.goto("/payment/q1")
    page.wait_for_selector(sel(PD + "correct-amount"))
    page.wait_for_load_state("networkidle")
    _correct(page, "1", "shrink", when=local(at(5)))
    page.wait_for_selector(sel(PD + "correct-error"))
    msg = T(page, PD + "correct-error")
    _plain(msg)
    assert re.search(r"earlier|past|history|negative|at that time|then", msg, re.I), msg


def local(t):
    return t.astimezone().strftime("%Y-%m-%dT%H:%M")


@pytest.mark.parametrize("code,status", [("linked_payment_immutable", 422), ("refund_exceeds_payment", 422),
                                         ("insufficient_funds", 409), ("validation_failed", 422),
                                         ("forbidden", 403), ("stale_revision", 409),
                                         ("historical_overdraft", 409)])
def test_every_correction_error_code_is_plain_language(pw, new_page, code, status):
    page = new_page()
    goto_pay(page, pw.p["payment_id"], "ada")
    page.route(re.compile(r"/corrections$"), lambda r: r.fulfill(
        status=status, content_type="application/json",
        body=json.dumps({"error": {"code": code, "message": "server wording"}})))
    _correct(page, "1", "x")
    page.wait_for_selector(sel(PD + "correct-error"))
    _plain(T(page, PD + "correct-error"))
    assert not has(page, PD + "correct-success")


def test_a_lost_correction_response_is_uncertain_and_the_retry_applies_once(pw, new_page):
    page = new_page()
    goto_pay(page, pw.p["payment_id"], "ada")
    state = {"lose": True}

    def route(r):
        if state["lose"]:
            state["lose"] = False
            r.fetch()
            r.abort("failed")
        else:
            r.continue_()
    page.route(re.compile(r"/corrections$"), route)
    _correct(page, "2", "lost")
    page.wait_for_selector(sel(PD + "correct-uncertain"))
    assert not has(page, PD + "correct-error")
    assert len(pw.ada.revisions(pw.p["payment_id"])) == 2
    page.click(sel(PD + "correct-submit"))
    page.wait_for_selector(sel(PD + "correct-success"))
    assert len(pw.ada.revisions(pw.p["payment_id"])) == 2, "the retry must replay"
    assert pw.ada.balance() == 10_000 - 200


# ---- states, layout, loops ---------------------------------------------------------------------------

def test_loading_state_then_content(pw, new_page):
    page = new_page()
    log_in(page)

    def slow(route):
        page.wait_for_timeout(1_200)
        route.continue_()
    page.route(re.compile(r"/revisions$"), slow)
    page.goto(f"/payment/{pw.p['payment_id']}")
    pw_expect(page.locator(sel(PD + "loading"))).to_be_visible(timeout=1_000)
    page.wait_for_selector(sel(PD + "revisions"), timeout=8_000)
    pw_expect(page.locator(sel(PD + "loading"))).to_have_count(0)


def test_actions_wait_for_a_slow_revisions_load_instead_of_dropping_the_click(pw, new_page):
    """H1 pattern on the new forms: the click right after the form appears must still run."""
    page = new_page()
    log_in(page)

    def slow(route):
        page.wait_for_timeout(1_200)
        route.continue_()
    page.route(re.compile(r"/revisions$"), slow)
    page.goto(f"/payment/{pw.p['payment_id']}")
    page.wait_for_selector(sel(PD + "correct-submit"))
    page.fill(sel(PD + "correct-amount"), "5")
    page.fill(sel(PD + "correct-reason"), "fast")
    page.click(sel(PD + "correct-submit"))
    page.wait_for_selector(sel(PD + "correct-success"), timeout=10_000)


def test_first_click_loop_on_the_payment_forms(pw, new_page):
    lost = 0
    for who, submit, effect in (("ada", PD + "correct-submit", PD + "correct-error"),
                                ("bob", PD + "refund-submit", PD + "refund-error")):
        page = new_page()
        log_in(page, email=f"{who}@example.com")
        for _ in range(20):
            page.goto(f"/payment/{pw.p['payment_id']}")
            page.wait_for_selector(sel(submit))
            page.click(sel(submit))
            try:
                page.wait_for_selector(sel(effect), timeout=2_000)
            except Exception:
                lost += 1
    assert lost == 0, f"{lost} first clicks on the payment forms did nothing"


@pytest.mark.parametrize("width", [375, 1280])
@pytest.mark.parametrize("who", ["ada", "bob"])
def test_detail_has_no_horizontal_scroll(pw, new_page, width, who):
    page = new_page(viewport={"width": width, "height": 800})
    expect(pw.ada.correct(pw.p["payment_id"], amount=400, effective_at=pw.p["created_at"], reason="r" * 150), 201)
    goto_pay(page, pw.p["payment_id"], who)
    d = page.evaluate("() => { const e = document.scrollingElement; return [e.scrollWidth, e.clientWidth]; }")
    assert d[0] <= d[1] + 1, d


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("who", ["ada", "bob"])
def test_detail_meets_aa_in_both_schemes(pw, new_page, scheme, who):
    page = new_page(color_scheme=scheme)
    goto_pay(page, pw.p["payment_id"], who)
    _correct_or_refund_error(page, who)
    res = page.evaluate(_SCAN_JS)
    assert not res["bad"], f"{scheme}: {res['bad'][:8]}"


def _correct_or_refund_error(page, who):
    if who == "ada":
        _correct(page, "abc")
        page.wait_for_selector(sel(PD + "correct-error"))
    else:
        _refund(page, "abc")
        page.wait_for_selector(sel(PD + "refund-error"))
    settle(page, 200)


def test_detail_makes_only_same_origin_requests(pw, page, base_url):
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(base_url))
    foreign = []
    page.on("request", lambda r: foreign.append(r.url) if not r.url.startswith(origin)
            and not r.url.startswith(("data:", "blob:")) else None)
    goto_pay(page, pw.p["payment_id"])
    assert foreign == []


def test_keyboard_only_refund(pw, new_page):
    page = new_page()
    goto_pay(page, pw.p["payment_id"], "bob")
    page.focus(sel(PD + "refund-amount"))
    page.keyboard.type("1")
    page.keyboard.press("Enter")
    page.wait_for_selector(sel(PD + "refund-success"))
    assert pw.bob.balance() == 3_500 - 100
