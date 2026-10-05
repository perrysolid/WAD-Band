"""UI upgrade item 2: the /history screen (balance as of a moment, paged statement over a snapshot).
Test ids are those in stage-4/RUN.md ('History screen')."""
from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest
from playwright.sync_api import expect as pw_expect

import pf_model as m
from pf_client import expect
from ui_kit import log_in, sel, settle
from test_ui_polish import _SCAN_JS

NOW = m.now().replace(second=0, microsecond=0)


def at(days=0, hours=0, minutes=0):
    return NOW - timedelta(days=days, hours=hours, minutes=minutes)


def local_input(t, tz: str) -> str:
    """The value a datetime-local input holds for instant t in zone tz."""
    return t.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%dT%H:%M")


def amount(page, tid: str) -> int:
    return int(page.get_attribute(sel(tid), "data-amount"))


@pytest.fixture
def hist(reset, api):
    """ada: opening 10300 -> p_1 -500 @-5d, p_2 +200 @-3d; a hold made @-2d (2000, 2 h left)."""
    fx = m.history_fixture([m.seeded_payment("p_1", "ada", "bob", 500, m.iso(at(5))),
                            m.seeded_payment("p_2", "bob", "ada", 200, m.iso(at(3))),
                            m.seeded_payment("p_3", "cy", "bob", 100, m.iso(at(1)))],
                           authorizations=[m.hold("a_1", "ada", "bob", 2_000, created_at=m.iso(at(2)),
                                                  expires_at=m.iso(m.now() + timedelta(hours=2)))])
    reset(fx)
    return fx


def _ready(page):
    page.wait_for_selector(sel("history-asof-submit"))
    page.wait_for_load_state("networkidle")


def _asof(page, t, tz="UTC"):
    _ready(page)
    page.fill(sel("history-asof-time"), local_input(t, tz))
    with page.expect_request(re.compile(r"/me\?")) as info:
        page.click(sel("history-asof-submit"))
    return info.value


def test_history_route_and_navigation(hist, page):
    log_in(page)
    for route in ("/", "/requests", "/split", "/authorizations", "/history"):
        page.goto(route)
        page.wait_for_selector(sel("current-user"))
        links = page.evaluate("() => [...document.querySelectorAll('nav a')].map(a => new URL(a.href).pathname)")
        assert "/history" in links, (route, links)
    page.goto("/history")
    page.wait_for_selector(sel("history-asof-time"))
    assert text_of(page, "current-handle") == "ada"


def text_of(page, tid):
    return (page.text_content(sel(tid)) or "").strip()


def test_signed_out_history_goes_to_login(hist, page):
    page.goto("/history")
    page.wait_for_selector(sel("login-submit"))


def test_history_is_json_404_without_html_accept(hist, control):
    r = control.get("/history", headers={"Accept": "application/json"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    h = control.get("/history", headers={"Accept": "text/html"})
    assert h.status_code == 200 and "text/html" in h.headers["content-type"]


# ---- balance as of -----------------------------------------------------------------------------

@pytest.mark.parametrize("tz,off", [("UTC", "+00:00"), ("Asia/Kolkata", "+05:30"),
                                    ("America/New_York", None), ("Pacific/Auckland", None)])
def test_as_of_is_sent_with_the_local_offset_and_the_balance_is_exact(hist, new_page, tz, off):
    page = new_page(timezone_id=tz)
    log_in(page)
    page.goto("/history")
    t = at(4)                                                   # between p_1 and p_2
    req = _asof(page, t, tz)
    q = parse_qs(urlsplit(req.url).query)
    sent = q["as_of"][0]
    assert re.search(r"[+-]\d\d:\d\d$|Z$", sent), f"explicit offset expected: {sent}"
    if off:
        assert sent.endswith(off), sent
    assert m.parse(sent) == t, (sent, t)
    pw_expect(page.locator(sel("history-asof-balance"))).to_have_attribute("data-amount", "9800")
    assert amount(page, "history-asof-available") == 9800
    assert sent in page.inner_text(sel("history-asof-echo"))


def test_as_of_offset_follows_daylight_saving(hist, new_page):
    page = new_page(timezone_id="America/New_York")
    log_in(page)
    page.goto("/history")
    seen = {}
    _ready(page)
    for label, day in (("winter", "2025-01-15T09:30"), ("summer", "2025-07-15T09:30")):
        page.fill(sel("history-asof-time"), day)
        with page.expect_request(re.compile(r"/me\?")) as info:
            page.click(sel("history-asof-submit"))
        seen[label] = parse_qs(urlsplit(info.value.url).query)["as_of"][0]
        pw_expect(page.locator(sel("history-asof-balance"))).to_have_attribute("data-amount", "10300")
    assert seen["winter"].endswith("-05:00") and seen["summer"].endswith("-04:00"), seen


def test_as_of_inclusive_at_the_exact_minute_of_a_payment(reset, new_page):
    t = at(2)
    reset(m.history_fixture([m.seeded_payment("p_1", "ada", "bob", 500, m.iso(t))]))
    page = new_page(timezone_id="Asia/Kolkata")
    log_in(page)
    page.goto("/history")
    _asof(page, t, "Asia/Kolkata")
    pw_expect(page.locator(sel("history-asof-balance"))).to_have_attribute("data-amount", "10000")
    _asof(page, t - timedelta(minutes=1), "Asia/Kolkata")
    pw_expect(page.locator(sel("history-asof-balance"))).to_have_attribute("data-amount", "10500")


def test_as_of_shows_held_and_available_when_the_api_gives_them(hist, page):
    log_in(page)
    page.goto("/history")
    _asof(page, at(1))                                         # hold exists (since -2d)
    pw_expect(page.locator(sel("history-asof-held"))).to_have_attribute("data-amount", "2000")
    assert amount(page, "history-asof-balance") == 10_000
    assert amount(page, "history-asof-available") == amount(page, "history-asof-balance") - 2000
    _asof(page, at(3, hours=1))                                # before the hold
    pw_expect(page.locator(sel("history-asof-balance"))).to_have_attribute("data-amount", "9800")
    if page.locator(sel("history-asof-held")).count():
        assert amount(page, "history-asof-held") == 0


def test_as_of_before_everything_is_the_opening_balance_and_future_the_current(hist, page):
    log_in(page)
    page.goto("/history")
    _asof(page, at(30))
    pw_expect(page.locator(sel("history-asof-balance"))).to_have_attribute("data-amount", "10300")
    _asof(page, NOW + timedelta(days=30))
    pw_expect(page.locator(sel("history-asof-balance"))).to_have_attribute("data-amount", "10000")


def test_balance_text_is_formatted_with_the_currency(hist, page):
    log_in(page)
    page.goto("/history")
    _asof(page, at(4))
    pw_expect(page.locator(sel("history-asof-balance"))).to_have_text("98.00 EUR")


def test_as_of_empty_input_shows_a_plain_language_error(hist, page):
    log_in(page)
    page.goto("/history")
    _ready(page)
    page.fill(sel("history-asof-time"), "")
    page.click(sel("history-asof-submit"))
    page.wait_for_selector(sel("history-asof-error"))
    msg = text_of(page, "history-asof-error")
    assert msg and not re.search(r"validation_failed|\b422\b|undefined|null", msg), msg


def test_422_and_500_from_the_api_are_plain_language(hist, page):
    log_in(page)
    page.goto("/history")
    _ready(page)
    page.route("**/me?as_of=*", lambda r: r.fulfill(
        status=422, content_type="application/json",
        body='{"error":{"code":"validation_failed","message":"as_of bad"}}'))
    page.fill(sel("history-asof-time"), local_input(at(4), "UTC"))
    page.click(sel("history-asof-submit"))
    page.wait_for_selector(sel("history-asof-error"))
    assert not re.search(r"validation_failed|\b422\b", text_of(page, "history-asof-error"))


def test_loading_state_then_result(hist, page):
    log_in(page)
    page.goto("/history")

    def slow(route):
        page.wait_for_timeout(1_200)
        route.continue_()
    page.route("**/me?as_of=*", slow)
    page.fill(sel("history-asof-time"), local_input(at(4), "UTC"))
    page.click(sel("history-asof-submit"))
    pw_expect(page.locator(sel("history-asof-loading"))).to_be_visible(timeout=800)
    pw_expect(page.locator(sel("history-asof-balance"))).to_have_attribute("data-amount", "9800")
    pw_expect(page.locator(sel("history-asof-loading"))).to_have_count(0)


def test_latest_as_of_wins_when_responses_arrive_out_of_order(hist, page):
    log_in(page)
    page.goto("/history")
    state = {"n": 0}

    def route(r):
        state["n"] += 1
        if state["n"] == 1:
            page.wait_for_timeout(2_000)                         # the first answer is slow
        r.continue_()
    page.route("**/me?as_of=*", route)
    page.fill(sel("history-asof-time"), local_input(at(30), "UTC"))     # opening 10300
    page.click(sel("history-asof-submit"))
    page.wait_for_timeout(200)
    page.fill(sel("history-asof-time"), local_input(NOW + timedelta(days=1), "UTC"))   # current 10000
    page.click(sel("history-asof-submit"))
    page.wait_for_timeout(3_000)
    assert amount(page, "history-asof-balance") == 10000, "a delayed earlier read overwrote the later one"


# ---- statement --------------------------------------------------------------------------------

def _statement(page):
    _ready(page)
    with page.expect_request(re.compile(r"/statement\?")) as info:
        page.click(sel("history-statement-submit"))
    return info.value


def _entries(page):
    return page.evaluate("""() => [...document.querySelectorAll("[data-testid^='history-entry-']")]
        .filter(e => /^history-entry-(?!parties)/.test(e.dataset.testid)).map(e => e.dataset.testid.slice(14))""")


def test_statement_default_window_matches_the_api(hist, page, api):
    log_in(page)
    page.goto("/history")
    req = _statement(page)
    q = parse_qs(urlsplit(req.url).query)
    assert q["limit"] == ["20"] and q["offset"] == ["0"] and "snapshot" not in q
    page.wait_for_selector(sel("history-entries"))
    st = api().authenticate("ada@example.com").statement_all()
    assert amount(page, "history-opening") == st["opening_balance"] == 10300
    assert amount(page, "history-closing") == st["closing_balance"] == 10000
    assert _entries(page) == [e["payment"]["payment_id"] for e in st["entries"]] == ["p_1", "p_2"]
    for e in st["entries"]:
        pid = e["payment"]["payment_id"]
        assert amount(page, f"history-delta-{pid}") == e["delta"]
        assert amount(page, f"history-balance-after-{pid}") == e["balance_after"]
        assert re.match(r"^[+\u2212-]", text_of(page, f"history-delta-{pid}")), text_of(page, f"history-delta-{pid}")
        parties = text_of(page, f"history-entry-parties-{pid}").lower()
        assert "bob" in parties and ("you" in parties or "ada" in parties), parties
    assert text_of(page, "history-delta-p_1").startswith(("-", "\u2212")) and "5.00" in text_of(page, "history-delta-p_1")
    assert text_of(page, "history-delta-p_2").startswith("+") or "2.00" in text_of(page, "history-delta-p_2")


def test_statement_is_only_my_own_payments(hist, new_page):
    page = new_page()
    log_in(page, email="cy@example.com")
    page.goto("/history")
    _statement(page)
    page.wait_for_selector(sel("history-entries"))
    assert _entries(page) == ["p_3"]


def test_range_is_half_open_and_matches_the_api(hist, new_page, api):
    page = new_page(timezone_id="Asia/Kolkata")
    log_in(page)
    page.goto("/history")
    page.fill(sel("history-from"), local_input(at(5), "Asia/Kolkata"))   # from inclusive: p_1 in
    page.fill(sel("history-to"), local_input(at(3), "Asia/Kolkata"))     # to exclusive: p_2 out
    req = _statement(page)
    q = parse_qs(urlsplit(req.url).query)
    assert m.parse(q["from"][0]) == at(5) and m.parse(q["to"][0]) == at(3)
    assert q["from"][0].endswith("+05:30")
    page.wait_for_selector(sel("history-entries"))
    assert _entries(page) == ["p_1"]
    st = api().authenticate("ada@example.com").statement(**{"from": m.iso(at(5)), "to": m.iso(at(3))})
    assert amount(page, "history-opening") == st["opening_balance"]
    assert amount(page, "history-closing") == st["closing_balance"]
    assert amount(page, "history-opening") + amount(page, "history-delta-p_1") == amount(page, "history-closing")


def test_empty_window_shows_the_empty_state_with_balances(hist, page):
    log_in(page)
    page.goto("/history")
    page.fill(sel("history-from"), local_input(at(40), "UTC"))
    page.fill(sel("history-to"), local_input(at(39), "UTC"))
    _statement(page)
    page.wait_for_selector(sel("history-empty"))
    assert page.locator(sel("history-entries")).count() == 0 or _entries(page) == []
    assert amount(page, "history-opening") == amount(page, "history-closing") == 10300


@pytest.fixture
def big(reset, api):
    pays = [m.seeded_payment(f"s_{i:03d}", "ada" if i % 2 else "bob", "bob" if i % 2 else "ada",
                             1 + i, m.iso(at(0, minutes=900 - 10 * i))) for i in range(25)]
    reset(m.history_fixture(pays, ending={"ada": 50_000, "bob": 50_000, "cy": 0}))
    return api().authenticate("ada@example.com")


def test_paging_uses_only_snapshot_limit_offset_and_stays_consistent(big, page):
    ada = big
    full = ada.statement_all()
    log_in(page)
    page.goto("/history")
    first = _statement(page)
    page.wait_for_selector(sel("history-entries"))
    ids1 = _entries(page)
    assert len(ids1) == 20 and ids1 == [e["payment"]["payment_id"] for e in full["entries"][:20]]
    info = text_of(page, "history-page-info")
    assert "1" in info and "20" in info, info
    closing = amount(page, "history-closing")
    opening = amount(page, "history-opening")
    assert closing == full["closing_balance"] and opening == full["opening_balance"]
    # money moves between the pages: a frozen snapshot must not notice
    expect(ada.pay("cy", 77), 201)
    assert page.locator(sel("history-prev")).count() == 0 or page.locator(sel("history-prev")).is_disabled()
    with page.expect_request(re.compile(r"/statement\?")) as nxt:
        page.click(sel("history-next"))
    q = parse_qs(urlsplit(nxt.value.url).query)
    assert set(q) == {"snapshot", "limit", "offset"}, f"later pages use ONLY snapshot+limit+offset: {q}"
    assert q["offset"] == ["20"] and q["limit"] == ["20"] and q["snapshot"][0]
    pw_expect(page.locator(sel("history-entry-s_024"))).to_be_visible()
    ids2 = _entries(page)
    assert ids2 == [e["payment"]["payment_id"] for e in full["entries"][20:]] and len(ids2) == 5
    assert "21" in text_of(page, "history-page-info") and "25" in text_of(page, "history-page-info")
    assert amount(page, "history-closing") == closing and amount(page, "history-opening") == opening
    for e in full["entries"][20:]:
        pid = e["payment"]["payment_id"]
        assert amount(page, f"history-balance-after-{pid}") == e["balance_after"]
    assert page.locator(sel("history-next")).count() == 0 or page.locator(sel("history-next")).is_disabled()
    with page.expect_request(re.compile(r"/statement\?")) as prv:
        page.click(sel("history-prev"))
    assert parse_qs(urlsplit(prv.value.url).query)["offset"] == ["0"]
    assert set(parse_qs(urlsplit(prv.value.url).query)) == {"snapshot", "limit", "offset"}
    pw_expect(page.locator(sel("history-entry-s_000"))).to_be_visible()
    assert _entries(page) == ids1, "going back shows the same frozen first page"
    assert first.url


def test_a_new_run_takes_a_new_snapshot(big, page):
    ada = big
    log_in(page)
    page.goto("/history")
    _statement(page)
    page.wait_for_selector(sel("history-entries"))
    expect(ada.pay("cy", 5), 201)
    q = parse_qs(urlsplit(_statement(page).url).query)
    assert "snapshot" not in q, "a fresh run is a first request"
    pw_expect(page.locator(sel("history-closing"))).to_have_attribute(
        "data-amount", str(ada.me()["balance"]))


def test_a_lost_snapshot_says_so_in_plain_words(big, page):
    log_in(page)
    page.goto("/history")
    _statement(page)
    page.wait_for_selector(sel("history-entries"))
    page.route(re.compile(r"/statement\?snapshot="), lambda r: r.fulfill(
        status=404, content_type="application/json",
        body='{"error":{"code":"not_found","message":"no such snapshot"}}'))
    page.click(sel("history-next"))
    page.wait_for_selector(sel("history-error"))
    msg = text_of(page, "history-error")
    assert re.search(r"no longer|again|expired", msg, re.I) and not re.search(r"not_found|404", msg), msg


def test_statement_422_is_plain_language(hist, page):
    log_in(page)
    page.goto("/history")
    page.route(re.compile(r"/statement\?"), lambda r: r.fulfill(
        status=422, content_type="application/json",
        body='{"error":{"code":"validation_failed","message":"bad from"}}'))
    page.click(sel("history-statement-submit"))
    page.wait_for_selector(sel("history-error"))
    assert not re.search(r"validation_failed|\b422\b", text_of(page, "history-error"))


def test_statement_loading_state(hist, page):
    log_in(page)
    page.goto("/history")

    def slow(route):
        page.wait_for_timeout(1_200)
        route.continue_()
    page.route(re.compile(r"/statement\?"), slow)
    page.click(sel("history-statement-submit"))
    pw_expect(page.locator(sel("history-loading"))).to_be_visible(timeout=800)
    page.wait_for_selector(sel("history-entries"))
    pw_expect(page.locator(sel("history-loading"))).to_have_count(0)


def test_money_formats_follow_the_currency(reset, page):
    reset(m.history_fixture([m.seeded_payment("p_1", "ada", "bob", 1500, m.iso(at(2)))],
                            ending={"ada": 10_000, "bob": 2_500, "cy": 500}, currency="JPY"))
    log_in(page)
    page.goto("/history")
    _statement(page)
    page.wait_for_selector(sel("history-entries"))
    assert text_of(page, "history-closing") == "10000 JPY"
    assert "." not in text_of(page, "history-delta-p_1") and "1500" in text_of(page, "history-delta-p_1")


# ---- layout, contrast, a11y ---------------------------------------------------------------------

@pytest.mark.parametrize("width", [375, 1280])
def test_history_has_no_horizontal_scroll(big, new_page, width):
    page = new_page(viewport={"width": width, "height": 800})
    log_in(page)
    page.goto("/history")
    _statement(page)
    page.wait_for_selector(sel("history-entries"))
    _asof(page, at(0, hours=1))
    settle(page, 300)
    dims = page.evaluate("() => { const d = document.scrollingElement; return [d.scrollWidth, d.clientWidth]; }")
    assert dims[0] <= dims[1] + 1, dims


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_history_meets_aa_contrast(big, new_page, scheme):
    page = new_page(color_scheme=scheme)
    log_in(page)
    page.goto("/history")
    _statement(page)
    page.wait_for_selector(sel("history-entries"))
    _asof(page, at(0, hours=1))
    settle(page, 300)
    res = page.evaluate(_SCAN_JS)
    assert not res["bad"], f"{scheme}: {res['bad'][:8]}"


def test_inputs_have_visible_labels_and_keyboard_works(hist, page):
    log_in(page)
    page.goto("/history")
    for tid in ("history-asof-time", "history-from", "history-to"):
        label = page.evaluate("""(tid) => { const e = document.querySelector(`[data-testid='${tid}']`);
            const l = e.labels && e.labels[0]; return l ? l.textContent.trim() : (e.getAttribute('aria-label') || ''); }""", tid)
        assert label, f"{tid} needs a label"
    page.focus(sel("history-asof-time"))
    page.fill(sel("history-asof-time"), local_input(at(4), "UTC"))
    page.keyboard.press("Enter")
    pw_expect(page.locator(sel("history-asof-balance"))).to_have_attribute("data-amount", "9800", timeout=5_000)


def test_history_makes_only_same_origin_requests(big, page, base_url):
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(base_url))
    foreign = []
    page.on("request", lambda r: foreign.append(r.url) if not r.url.startswith(origin)
            and not r.url.startswith(("data:", "blob:")) else None)
    log_in(page)
    page.goto("/history")
    _statement(page)
    page.wait_for_selector(sel("history-entries"))
    _asof(page, at(1))
    assert foreign == []


def test_history_page_carries_the_csp_and_no_external_urls(hist, control):
    r = control.get("/history", headers={"Accept": "text/html"})
    assert "default-src 'self'" in r.headers.get("content-security-policy", "")
    assert not re.search(r"""(?:src|href)=["']https?://""", r.text), "external URL in the shell"
