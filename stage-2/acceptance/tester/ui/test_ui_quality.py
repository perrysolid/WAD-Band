"""S2-U13 self-contained assets + CSP, and the objectively checkable parts of S2-U14:
no horizontal scroll at 375 px and 1280 px, visible labels, visible keyboard focus,
WCAG AA text contrast, a viewport meta tag. The subjective walk is the reviewer's."""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlsplit

import pytest

import pf_model as m
from pf_client import expect
from ui_kit import fill_pay, log_in, sel, settle

ROUTES = [("/", "pay-submit"), ("/requests", "incoming-list"), ("/split", "split-submit"),
          ("/authorizations", "authorize-submit")]
PUBLIC = [("/login", "login-submit"), ("/signup", "signup-submit")]


@pytest.fixture
def busy(reset, api):
    """Long handles, long notes, huge amounts and items in every state."""
    long = "a_very_long_handle_x"   # 20 characters
    users = [m.user("ada", 900_000_000_000, display_name="Ada Lovelace-Byron of Ockham"),
             m.user(long, 5_000_000), m.BOB, m.CY]
    past = m.iso(m.now() - timedelta(hours=2))
    reset(m.fixture(users, authorizations=[
        m.hold("a_1", "ada", long, 1_000_000_000, note="N" * 200),
        m.hold("a_2", long, "ada", 999_999, note="deposit for the flat"),
        m.hold("a_3", "ada", "bob", 1, status="captured"),
        m.hold("a_4", "ada", "bob", 2, status="voided"),
        m.hold("a_5", "ada", "bob", 3, expires_at=past)]))
    ada = api().authenticate("ada@example.com")
    expect(ada.pay(long, 1_000_000_000, note="W" * 200), 201)
    expect(ada.pay("bob", 1, note="\U0001F37D" * 50, visibility="private"), 201)
    expect(api().authenticate(f"{long}@example.com").ask("ada", 1_000_000_000,
                                                          note="x" * 200), 201)
    expect(ada.ask(long, 77), 201)
    expect(ada.split(100, ["ada", long, "bob"]), 201)


def _no_horizontal_scroll(page) -> None:
    overflow = page.evaluate("""() => {
        const d = document.scrollingElement || document.documentElement;
        return [d.scrollWidth, d.clientWidth, window.innerWidth]; }""")
    assert overflow[0] <= overflow[1] + 1, f"horizontal page scroll: {overflow}"


@pytest.mark.parametrize("width", [375, 1280])
@pytest.mark.parametrize("route,anchor", ROUTES + PUBLIC)
def test_no_horizontal_scroll(busy, new_page, width, route, anchor):
    page = new_page(viewport={"width": width, "height": 800})
    if route not in ("/login", "/signup"):
        log_in(page)
    page.goto(route)
    page.wait_for_selector(sel(anchor), state="attached")
    settle(page, 300)
    _no_horizontal_scroll(page)
    if route == "/split":
        page.fill(sel("split-amount"), "1000000.00")
        page.fill(sel("split-handles"), "ada,a_very_long_handle_x,bob,cy")
        page.wait_for_selector(sel("split-preview"))
        _no_horizontal_scroll(page)
    if route == "/":
        fill_pay(page, handle="nobody", amount="1.00", note="Z" * 200)
        page.click(sel("pay-submit"))
        page.wait_for_selector(sel("pay-error"))
        _no_horizontal_scroll(page)


@pytest.mark.parametrize("route,anchor", ROUTES + PUBLIC)
def test_primary_controls_fit_and_are_reachable_at_375(busy, new_page, route, anchor):
    page = new_page(viewport={"width": 375, "height": 740})
    if route not in ("/login", "/signup"):
        log_in(page)
    page.goto(route)
    page.wait_for_selector(sel(anchor), state="attached")
    box = page.locator(sel(anchor)).bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= 376, box
    assert box["height"] >= 24, f"{anchor} is a tiny touch target: {box}"


_LABEL_JS = """() => {
  const out = [];
  for (const el of document.querySelectorAll('input, select, textarea')) {
    if (el.type === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const visible = n => { if (!n) return false; const b = n.getBoundingClientRect();
      const s = getComputedStyle(n); return b.width > 0 && b.height > 0 &&
      s.visibility !== 'hidden' && s.display !== 'none' && (n.innerText || '').trim() !== ''; };
    let ok = false;
    for (const l of (el.labels || [])) if (visible(l)) ok = true;
    const ids = (el.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean);
    for (const id of ids) if (visible(document.getElementById(id))) ok = true;
    if (!ok) out.push(el.getAttribute('data-testid') || el.name || el.id || el.outerHTML.slice(0, 80));
  }
  return out;
}"""


@pytest.mark.parametrize("route,anchor", ROUTES + PUBLIC)
def test_every_input_has_a_visible_label(busy, page, route, anchor):
    """[S2-U14] a visible <label> (or aria-labelledby to visible text); placeholders don't count."""
    if route not in ("/login", "/signup"):
        log_in(page)
    page.goto(route)
    page.wait_for_selector(sel(anchor), state="attached")
    settle(page, 200)
    missing = page.evaluate(_LABEL_JS)
    assert missing == [], f"inputs without a visible label on {route}: {missing}"


_FOCUS_JS = """(tid) => {
  const el = document.querySelector(`[data-testid='${tid}']`);
  const s = getComputedStyle(el);
  return [s.outlineStyle, s.outlineWidth, s.outlineColor, s.boxShadow, s.borderColor,
          s.backgroundColor].join('|');
}"""


@pytest.mark.parametrize("route,targets", [
    ("/", ["pay-handle", "pay-amount", "pay-submit", "wallet-refresh"]),
    ("/login", ["login-email", "login-submit"]),
    ("/authorizations", ["authorize-amount", "authorize-submit"])])
def test_keyboard_focus_is_visible(busy, page, route, targets):
    """[S2-U14] tabbing onto a control changes how it looks (outline, ring or border)."""
    if route != "/login":
        log_in(page)
    page.goto(route)
    page.wait_for_selector(sel(targets[0]))
    for tid in targets:
        page.evaluate("() => document.activeElement && document.activeElement.blur()")
        before = page.evaluate(_FOCUS_JS, tid)
        reached = False
        for _ in range(60):
            page.keyboard.press("Tab")
            if page.evaluate("t => document.activeElement && "
                             "document.activeElement.getAttribute('data-testid') === t", tid):
                reached = True
                break
        assert reached, f"{tid} is not reachable with Tab on {route}"
        after = page.evaluate(_FOCUS_JS, tid)
        assert after != before, f"no visible focus indicator on {tid}: {after}"
        o_style, o_width = after.split("|")[:2]
        shadow = after.split("|")[3]
        assert (o_style != "none" and o_width not in ("0px", "")) or shadow != "none" or \
            after.split("|")[4] != before.split("|")[4], after


_CONTRAST_JS = """(tid) => {
  const el = document.querySelector(`[data-testid='${tid}']`);
  if (!el) return null;
  const parse = c => { const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x)); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const lum = ([r, g, b]) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 :
    Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  let bg = null;
  for (let n = el; n; n = n.parentElement) {
    const c = parse(getComputedStyle(n).backgroundColor);
    if (c && c[3] > 0.5) { bg = c; break; }
    if (getComputedStyle(n).backgroundImage !== 'none') return {skip: 'background image'};
  }
  bg = bg || [255, 255, 255, 1];
  const s = getComputedStyle(el);
  const fg = parse(s.color);
  const L1 = lum(fg), L2 = lum(bg);
  const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
  const size = parseFloat(s.fontSize), bold = parseInt(s.fontWeight) >= 700;
  const large = size >= 24 || (bold && size >= 18.66);
  return {ratio, need: large ? 3 : 4.5, fg: s.color, bg: bg.join(',')};
}"""


@pytest.mark.parametrize("route,tids", [
    ("/", ["wallet-available", "wallet-balance", "wallet-held", "pay-submit", "current-user",
           "current-handle", "logout-button", "wallet-refresh", "request-submit"]),
    ("/requests", ["current-user", "logout-button"]),
    ("/split", ["split-submit"]),
    ("/authorizations", ["authorize-submit", "wallet-available"]),
    ("/login", ["login-submit"]), ("/signup", ["signup-submit"])])
def test_text_contrast_meets_wcag_aa(busy, page, route, tids):
    if route not in ("/login", "/signup"):
        log_in(page)
    page.goto(route)
    page.wait_for_selector(sel(tids[0]))
    settle(page, 200)
    bad = {}
    for tid in tids:
        r = page.evaluate(_CONTRAST_JS, tid)
        if r and "ratio" in r and r["ratio"] + 1e-6 < r["need"]:
            bad[tid] = r
    assert not bad, f"contrast below WCAG AA on {route}: {bad}"


def test_error_text_contrast(busy, page):
    log_in(page)
    page.goto("/")
    fill_pay(page, handle="nobody", amount="1.00")
    page.click(sel("pay-submit"))
    page.wait_for_selector(sel("pay-error"))
    r = page.evaluate(_CONTRAST_JS, "pay-error")
    assert r is None or "skip" in r or r["ratio"] >= r["need"], r


@pytest.mark.parametrize("route,anchor", ROUTES + PUBLIC)
def test_every_request_stays_on_the_service_origin(busy, page, base_url, route, anchor):
    """[S2-U13] fonts, scripts, styles and images all come from the image itself."""
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(base_url))
    seen = []
    page.on("request", lambda r: seen.append(r.url))
    if route not in ("/login", "/signup"):
        log_in(page)
    page.goto(route)
    page.wait_for_selector(sel(anchor), state="attached")
    settle(page, 300)
    foreign = [u for u in seen if not (u.startswith(origin) or u.startswith("data:")
                                       or u.startswith("blob:"))]
    assert foreign == [], foreign


@pytest.mark.parametrize("route", ["/", "/login"])
def test_document_basics(busy, page, route):
    if route != "/login":
        log_in(page)
    page.goto(route)
    meta = page.get_attribute("meta[name='viewport']", "content") or ""
    assert "width=device-width" in meta, f"viewport meta missing: {meta!r}"
    assert (page.title() or "").strip(), "the page needs a title"
    assert page.get_attribute("html", "lang"), "html lang attribute missing"


def test_uses_a_web_font_or_a_deliberate_stack(busy, page):
    """[S2-U14, D36] a consistent type system: the body font is the same on every screen."""
    log_in(page)
    fams = set()
    for route, anchor in ROUTES:
        page.goto(route)
        page.wait_for_selector(sel(anchor), state="attached")
        fams.add(page.evaluate("() => getComputedStyle(document.body).fontFamily"))
    assert len(fams) == 1, fams
    assert "Times" not in next(iter(fams)), fams


def test_holds_and_closed_states_are_distinguishable(busy, page):
    """[S2 visual direction] open, captured, voided and expired holds don't look identical."""
    log_in(page)
    page.goto("/authorizations")
    page.wait_for_selector(sel("authorization-item-a_5"))
    looks = {}
    for aid in ("a_1", "a_3", "a_4", "a_5"):
        looks[aid] = page.eval_on_selector(sel(f"authorization-item-{aid}"),
                                           "e => e.innerText.replace(/[0-9.,:TZ+\\-]/g, '')")
    assert len(set(looks.values())) == 4, f"statuses are not told apart in words: {looks}"
