"""UI upgrade item 1: visual polish. Everything new is checked by role/semantics (no testids
are specified for avatars, icons, relative time or toasts): sentences from the viewer's point
of view, privacy icon with an accessible name, relative time with the exact time on hover,
polite toasts that never shadow *-error/*-uncertain or steal focus, dark mode with AA contrast,
reduced motion, a bottom tab bar at <=600 px with the pay form first, no external requests.
The specified testids and flows are asserted unchanged by the existing U1-U14 suites."""
from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect as pw_expect

import pf_model as m
from pf_client import expect
from ui_kit import fill_pay, log_in, sel, settle, text, wait_wallet

ROUTES = ["/", "/requests", "/split", "/authorizations"]


@pytest.fixture
def seeded(reset):
    reset(m.fixture([m.ADA, m.BOB, m.CY, m.user("dee", 0, display_name="Dee")],
                    operators=["u_ada"]))


def _client(api, handle):
    return api().authenticate(f"{handle}@example.com")


def _item_text(page, pid: str) -> str:
    page.wait_for_selector(sel(f"activity-item-{pid}"))
    return page.inner_text(sel(f"activity-item-{pid}"))


# ---- sentences from the viewer's point of view --------------------------------------------------

def test_sent_and_received_sentences(seeded, page, api):
    p = expect(_client(api, "ada").pay("bob", 2_500, note="dinner"), 201).json()
    log_in(page)                                      # ada: the sender
    page.goto("/")
    t = _item_text(page, p["payment_id"])
    assert re.search(r"\byou\b.*\bpaid\b.*\bbob\b", t, re.I | re.S), t
    assert not re.search(r"\bada\b.*\bpaid\b.*\byou\b", t, re.I | re.S), t
    pid = p["payment_id"]
    assert text(page, f"activity-amount-{pid}").strip() == "25.00 EUR"
    assert text(page, f"activity-note-{pid}") == "dinner"
    assert "ada" in text(page, f"activity-parties-{pid}") and "bob" in text(page, f"activity-parties-{pid}")


def test_received_sentence(seeded, new_page, api):
    p = expect(_client(api, "ada").pay("bob", 2_500), 201).json()
    page = new_page()
    log_in(page, email="bob@example.com")
    page.goto("/")
    t = _item_text(page, p["payment_id"])
    assert re.search(r"\bada\b.*\bpaid\b.*\byou\b", t, re.I | re.S), t


def test_third_party_sentence_names_both_people(seeded, new_page, api):
    p = expect(_client(api, "ada").pay("bob", 100), 201).json()
    page = new_page()
    log_in(page, email="cy@example.com")
    page.goto("/")
    t = _item_text(page, p["payment_id"])
    assert re.search(r"\bada\b.*\bpaid\b.*\bbob\b", t, re.I | re.S), t
    assert not re.search(r"\byou\b", t, re.I), f"a third party is not 'you': {t}"


def test_request_capture_and_refund_are_worded_sensibly(seeded, new_page, api):
    ada, bob = _client(api, "ada"), _client(api, "bob")
    rq = expect(bob.ask("ada", 300), 201).json()
    rp = expect(ada.pay_request(rq["request_id"]), 201).json()
    a = expect(ada.authorize("bob", 400), 201).json()
    cap = expect(bob.capture(a["authorization_id"], {"amount": 150}), 201).json()
    ref = expect(bob.refund(rp["payment_id"], 100), 201).json()
    page = new_page()
    log_in(page)
    page.goto("/")
    t_req = _item_text(page, rp["payment_id"])
    t_cap = _item_text(page, cap["payment_id"])
    t_ref = _item_text(page, ref["payment_id"])
    assert re.search(r"\byou\b.*\bpaid\b|\bpaid\b.*\bbob\b", t_req, re.I | re.S), t_req
    assert re.search(r"request", t_req, re.I), f"a request payment should say so: {t_req}"
    assert re.search(r"captur|hold|authori[sz]", t_cap, re.I), f"a capture should say so: {t_cap}"
    assert re.search(r"refund", t_ref, re.I) and re.search(r"\byou\b", t_ref, re.I), t_ref
    for t in (t_req, t_cap, t_ref):
        assert not re.search(r"\bnull\b|\bundefined\b|\[object|\bp_\d+\b|\bu_\w+\b", t), t
    pw = page.locator(sel(f"activity-item-{ref['payment_id']}"))
    assert pw.get_attribute("data-visibility") == "public"


def test_empty_note_does_not_break_the_sentence(seeded, page, api):
    p = expect(_client(api, "ada").pay("bob", 1), 201).json()
    log_in(page)
    page.goto("/")
    t = _item_text(page, p["payment_id"])
    assert text(page, f"activity-note-{p['payment_id']}") == ""
    assert not re.search(r"\bnull\b|\bundefined\b", t)


# ---- privacy icon, avatars, time -----------------------------------------------------------------

_ICON_JS = """(tid) => {
  const root = document.querySelector(`[data-testid='${tid}']`);
  const names = [];
  for (const el of root.querySelectorAll('*')) {
    const label = el.getAttribute('aria-label') || el.getAttribute('title') ||
      (el.tagName.toLowerCase() === 'title' ? el.textContent : '');
    if (label) names.push([el.tagName.toLowerCase(), el.getAttribute('role'), label]);
    if (el.matches('.sr-only,.visually-hidden') && el.textContent.trim())
      names.push(['sr', null, el.textContent.trim()]);
  }
  return names;
}"""


def test_privacy_icon_has_an_accessible_name(seeded, page, api):
    pub = expect(_client(api, "ada").pay("bob", 10), 201).json()
    priv = expect(_client(api, "ada").pay("bob", 11, visibility="private"), 201).json()
    log_in(page)
    page.goto("/")
    for p, word, other in ((priv, "private", "public"), (pub, "public", "private")):
        _item_text(page, p["payment_id"])
        names = page.evaluate(_ICON_JS, f"activity-item-{p['payment_id']}")
        joined = " | ".join(n[2] for n in names).lower()
        assert word in joined, f"no accessible '{word}' marker on {p['payment_id']}: {names}"
        assert page.locator(f"{sel('activity-item-' + p['payment_id'])} svg").count() >= 1, \
            "the privacy marker should be an icon (inline svg)"
        assert page.get_attribute(sel(f"activity-item-{p['payment_id']}"), "data-visibility") == word
        assert other not in joined.replace(word, "")


def test_initials_avatars_are_decorative_or_named(seeded, page, api):
    p = expect(_client(api, "ada").pay("bob", 10), 201).json()
    log_in(page)
    page.goto("/")
    _item_text(page, p["payment_id"])
    # an avatar must not leak raw handles into the accessible text twice nor be an unlabeled image
    bad = page.evaluate("""() => [...document.querySelectorAll('img:not([alt]), svg[role=img]:not([aria-label]):not([aria-labelledby])')]
                            .map(e => e.outerHTML.slice(0, 80))""")
    assert bad == [], bad


def test_relative_time_with_exact_time_on_hover(seeded, page, api):
    p = expect(_client(api, "ada").pay("bob", 10), 201).json()
    old = expect(_client(api, "ada").pay("bob", 12), 201).json()
    log_in(page)
    page.goto("/")
    _item_text(page, p["payment_id"])
    item = page.locator(sel(f"activity-item-{p['payment_id']}"))
    t = item.locator("time")
    assert t.count() >= 1, "the feed item should use a <time> element"
    dt = t.first.get_attribute("datetime")
    assert dt and abs((m.parse(dt) - m.parse(p["created_at"])).total_seconds()) < 2, dt
    assert re.search(r"just now|\b\d+\s*(s|sec|second|m|min|minute|h|hour|d|day)s?\b.*\bago\b|\bnow\b",
                     t.first.inner_text(), re.I), t.first.inner_text()
    exact = t.first.get_attribute("title") or item.get_attribute("title") or ""
    assert re.search(r"\d{4}|\d{1,2}:\d{2}", exact), f"exact time on hover (title) expected, got {exact!r}"
    assert old["payment_id"]


def test_relative_time_for_old_payments(reset, page, api):
    reset(m.history_fixture([m.seeded_payment("p_1", "ada", "bob", 5, m.iso(m.now() - timedelta(days=3))),
                             m.seeded_payment("p_2", "ada", "bob", 6, m.iso(m.now() - timedelta(minutes=7)))]))
    log_in(page)
    page.goto("/")
    t1 = page.inner_text(f"{sel('activity-item-p_1')} time")
    t2 = page.inner_text(f"{sel('activity-item-p_2')} time")
    assert re.search(r"3\s*(d|day)|3 days", t1, re.I), t1
    assert re.search(r"7\s*(m|min)", t2, re.I), t2


# ---- toasts -------------------------------------------------------------------------------------

def test_success_toast_is_polite_and_does_not_steal_focus_or_shadow_testids(seeded, page):
    log_in(page)
    page.goto("/")
    wait_wallet(page, 10_000)
    fill_pay(page, handle="bob", amount="3.00", note="toast")
    page.click(sel("pay-submit"))
    wait_wallet(page, 9_700)
    live = page.locator("[aria-live='polite'], [role='status']")
    pw_expect(live.filter(has_text=re.compile(r"\S"))).not_to_have_count(0, timeout=3_000)
    assert page.evaluate("() => document.activeElement && document.activeElement.closest('[role=status],[aria-live]')") is None, \
        "a toast must not take focus"
    active = page.evaluate("() => document.activeElement && (document.activeElement.dataset.testid || document.activeElement.tagName)")
    assert active in ("pay-submit", "BODY", "BUTTON"), active
    for tid in ("pay-error", "pay-uncertain", "request-error", "authorize-error"):
        assert page.locator(sel(tid)).count() == 0, tid
    ids = page.evaluate("() => [...document.querySelectorAll('[data-testid]')].map(e => e.dataset.testid)")
    assert len(ids) == len(set(ids)), f"duplicate testids: {sorted({i for i in ids if ids.count(i) > 1})}"
    assert page.locator(sel("pay-submit")).is_enabled()


def test_toast_goes_away_without_user_action(seeded, page):
    log_in(page)
    page.goto("/")
    fill_pay(page, handle="bob", amount="1.00")
    page.click(sel("pay-submit"))
    wait_wallet(page, 9_900)
    done = re.compile(r"paid|sent|success|done|complete|created|requested|submitted", re.I)
    pw_expect(page.locator("[role='status'], [aria-live='polite']").filter(has_text=done)
              ).to_have_count(0, timeout=15_000)


def test_toast_does_not_hide_the_forms_error_state(seeded, page):
    log_in(page)
    page.goto("/")
    fill_pay(page, handle="nobody", amount="1.00")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-error"))
    assert page.is_visible(sel("pay-error"))
    assert page.locator(sel("pay-uncertain")).count() == 0


# ---- dark mode, contrast, reduced motion ---------------------------------------------------------

_SCAN_JS = """() => {
  const parse = c => { const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x)); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const lum = ([r, g, b]) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 :
    Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const bad = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const n = walker.currentNode; if (!n.textContent.trim()) continue;
    const el = n.parentElement; if (!el || seen.has(el)) continue; seen.add(el);
    const st = getComputedStyle(el), r = el.getBoundingClientRect();
    if (st.visibility === 'hidden' || st.display === 'none' || r.width === 0 || r.height === 0) continue;
    if (el.closest('[hidden], .sr-only, .visually-hidden') ) continue;
    if (el.disabled || el.closest(':disabled')) continue;
    let bg = null, skip = false;
    for (let a = el; a; a = a.parentElement) {
      const s = getComputedStyle(a);
      if (s.backgroundImage !== 'none') { skip = true; break; }
      const c = parse(s.backgroundColor); if (c && c[3] > 0.6) { bg = c; break; }
    }
    if (skip) continue;
    bg = bg || parse(getComputedStyle(document.documentElement).backgroundColor) || [255, 255, 255, 1];
    const fg = parse(st.color); const a = fg[3];
    const mix = fg.slice(0, 3).map((v, i) => v * a + bg[i] * (1 - a));
    const L1 = lum(mix), L2 = lum(bg);
    const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const size = parseFloat(st.fontSize), bold = parseInt(st.fontWeight) >= 700;
    const need = (size >= 24 || (bold && size >= 18.66)) ? 3 : 4.5;
    if (ratio + 1e-6 < need) bad.push([el.tagName, (n.textContent.trim()).slice(0, 30), +ratio.toFixed(2), need]);
  }
  return {bad, bodyBg: getComputedStyle(document.body).backgroundColor};
}"""


@pytest.fixture
def lively(reset, api):
    reset(m.fixture([m.ADA, m.BOB, m.CY], authorizations=[
        m.hold("a_1", "ada", "bob", 1_000), m.hold("a_2", "bob", "ada", 500)]))
    ada, bob = _client(api, "ada"), _client(api, "bob")
    expect(ada.pay("bob", 500, note="coffee", visibility="private"), 201)
    expect(bob.ask("ada", 200), 201)
    expect(ada.ask("bob", 30), 201)


def _bg_luma(css: str) -> float:
    r, g, b = [float(x) for x in re.findall(r"[\d.]+", css)[:3]]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("route", ROUTES + ["/login", "/signup"])
def test_every_text_meets_aa_in_light_and_dark(lively, new_page, scheme, route):
    page = new_page(color_scheme=scheme)
    if route not in ("/login", "/signup"):
        log_in(page)
    page.goto(route)
    settle(page, 300)
    res = page.evaluate(_SCAN_JS)
    assert not res["bad"], f"{scheme} {route}: contrast below AA: {res['bad'][:8]}"


def test_dark_mode_follows_the_system_preference(lively, new_page):
    lum = {}
    for scheme in ("light", "dark"):
        page = new_page(color_scheme=scheme)
        log_in(page)
        page.goto("/")
        settle(page, 300)
        lum[scheme] = _bg_luma(page.evaluate(_SCAN_JS)["bodyBg"])
    assert lum["dark"] < 90 < 160 < lum["light"] or lum["dark"] + 60 < lum["light"], lum


def test_error_state_meets_aa_in_dark(seeded, new_page):
    page = new_page(color_scheme="dark")
    log_in(page)
    page.goto("/")
    fill_pay(page, handle="nobody", amount="1.00")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-error"))
    res = page.evaluate(_SCAN_JS)
    assert not res["bad"], res["bad"][:5]


def test_reduced_motion_is_respected(lively, new_page):
    page = new_page(reduced_motion="reduce")
    log_in(page)
    page.goto("/")
    settle(page, 300)
    long = page.evaluate("""() => { const out = [];
      for (const e of document.querySelectorAll('body *')) { const s = getComputedStyle(e);
        const d = [s.animationDuration, s.transitionDuration].join(',').split(',')
          .map(x => x.trim()).map(x => x.endsWith('ms') ? parseFloat(x) / 1000 : parseFloat(x));
        if (d.some(v => v > 0.05)) out.push([e.tagName, e.className.toString().slice(0, 30), s.animationDuration, s.transitionDuration]);
        if (s.animationIterationCount === 'infinite' && parseFloat(s.animationDuration) > 0) out.push(['infinite', e.tagName]);
      } return out.slice(0, 6); }""")
    assert long == [], f"motion must be (near) zero under prefers-reduced-motion: {long}"
    fill_pay(page, handle="bob", amount="1.00")
    page.click(sel("pay-submit"))
    wait_wallet(page, 9_400, held=1_000)


# ---- narrow screens: bottom tab bar, pay form first ---------------------------------------------

def _nav_info(page):
    return page.evaluate("""() => [...document.querySelectorAll('nav')].map(n => {
        const s = getComputedStyle(n), r = n.getBoundingClientRect();
        return {fixed: s.position === 'fixed' || s.position === 'sticky', top: r.top, bottom: r.bottom,
                width: r.width, links: [...n.querySelectorAll('a')].map(a => new URL(a.href).pathname),
                vh: window.innerHeight}; })""")


@pytest.mark.parametrize("route", ROUTES)
def test_bottom_tab_bar_on_narrow_screens(lively, new_page, route):
    page = new_page(viewport={"width": 375, "height": 740})
    log_in(page)
    page.goto(route)
    settle(page, 300)
    bars = [n for n in _nav_info(page) if n["fixed"] and n["bottom"] >= n["vh"] - 4 and n["top"] > n["vh"] / 2]
    assert bars, f"expected a bottom navigation bar at 375 px: {_nav_info(page)}"
    paths = set(bars[0]["links"])
    assert {"/", "/requests", "/split", "/authorizations"} <= paths, paths
    assert bars[0]["width"] >= 370


def test_no_bottom_bar_on_wide_screens(lively, new_page):
    page = new_page(viewport={"width": 1280, "height": 800})
    log_in(page)
    page.goto("/")
    settle(page, 300)
    assert not [n for n in _nav_info(page) if n["fixed"] and n["top"] > n["vh"] / 2], _nav_info(page)


def test_pay_form_comes_first_at_375_and_other_forms_are_in_the_dom(lively, new_page):
    page = new_page(viewport={"width": 375, "height": 740})
    log_in(page)
    page.goto("/")
    page.wait_for_selector(sel("pay-submit"))
    pay = page.locator(sel("pay-submit")).bounding_box()
    assert pay and pay["y"] < 740 and pay["y"] >= 0, "the pay form should be on the first screen"
    for tid in ("request-handle", "request-amount", "request-note", "request-submit",
                "authorize-handle", "authorize-amount", "authorize-note", "authorize-visibility",
                "authorize-submit"):
        assert page.locator(sel(tid)).count() == 1, f"{tid} must stay in the DOM"
    assert page.locator(sel("pay-handle")).bounding_box()["y"] < page.locator(sel("request-handle")).evaluate(
        "e => e.getBoundingClientRect().top + window.scrollY") + 1


def _open_form(page, prefix: str) -> None:
    """Reveal a collapsed form by the least possible interaction: one click on a tab/summary."""
    if page.locator(sel(f"{prefix}-handle")).is_visible():
        return
    word = "request" if prefix == "request" else "hold|authori"
    tab = page.get_by_role("tab", name=re.compile(word, re.I))
    if tab.count():
        tab.first.click()
    else:
        page.locator("summary", has_text=re.compile(word, re.I)).first.click()
    pw_expect(page.locator(sel(f"{prefix}-handle"))).to_be_visible()


def test_request_and_authorize_forms_work_after_opening_their_tab_at_375(lively, new_page):
    page = new_page(viewport={"width": 375, "height": 740})
    log_in(page)
    page.goto("/")
    page.wait_for_selector(sel("pay-submit"))
    _open_form(page, "request")
    page.fill(sel("request-handle"), "bob")
    page.fill(sel("request-amount"), "4.00")
    page.fill(sel("request-note"), "tab")
    page.click(sel("request-submit"))
    settle(page, 500)
    assert page.locator(sel("request-error")).count() == 0
    _open_form(page, "authorize")
    page.fill(sel("authorize-handle"), "cy")
    page.fill(sel("authorize-amount"), "1.00")
    page.click(sel("authorize-submit"))
    wait_wallet(page, 9_500, held=1_000 + 100)


def test_keyboard_reaches_every_tab_and_control_at_375(lively, new_page):
    page = new_page(viewport={"width": 375, "height": 740})
    log_in(page)
    page.goto("/")
    page.wait_for_selector(sel("pay-submit"))
    seen = set()
    for _ in range(60):
        page.keyboard.press("Tab")
        seen.add(page.evaluate("() => document.activeElement && (document.activeElement.dataset.testid || document.activeElement.tagName)"))
    assert {"pay-handle", "pay-amount", "pay-submit"} <= seen, seen


# ---- no external requests --------------------------------------------------------------------------

def test_every_screen_makes_only_same_origin_requests(lively, new_page, base_url):
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(base_url))
    for scheme in ("light", "dark"):
        page = new_page(color_scheme=scheme, viewport={"width": 375, "height": 740})
        foreign = []
        page.on("request", lambda r: foreign.append(r.url) if not r.url.startswith(origin)
                and not r.url.startswith(("data:", "blob:")) else None)
        log_in(page)
        for route in ROUTES:
            page.goto(route)
            settle(page, 200)
        assert foreign == [], foreign


def test_the_polish_does_not_remove_any_specified_testid(lively, page):
    log_in(page)
    page.goto("/")
    page.wait_for_selector(sel("pay-submit"))
    for tid in ["current-user", "current-handle", "logout-button", "wallet-balance", "wallet-available",
                "wallet-held", "wallet-refresh", "pay-handle", "pay-amount", "pay-note", "pay-visibility",
                "pay-submit", "request-handle", "request-amount", "request-note", "request-submit",
                "authorize-handle", "authorize-amount", "authorize-note", "authorize-visibility",
                "authorize-submit", "activity-list"]:
        assert page.locator(sel(tid)).count() == 1, tid
    assert page.get_attribute(sel("current-handle"), "data-testid") == "current-handle"
    assert text(page, "current-handle").strip() == "ada"
