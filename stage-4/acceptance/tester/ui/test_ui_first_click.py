"""Robustness: the first click right after a testid appears must not be lost (hidden suites click
as soon as the element exists). For every screen: goto, wait for the submit button, click at once,
and the form must react (an error element for the empty form). Repeated N times."""
from __future__ import annotations

import pytest

import pf_model as m
from ui_kit import log_in, sel

N = 20

CASES = [
    ("/", "pay-submit", "pay-error"),
    ("/", "request-submit", "request-error"),
    ("/", "authorize-submit", "authorize-error"),
    ("/authorizations", "authorize-submit", "authorize-error"),
    ("/split", "split-submit", "split-error"),
    ("/history", "history-asof-submit", "history-asof-error"),
    ("/history", "history-statement-submit", "history-opening"),
]


@pytest.mark.parametrize("route,button,effect", CASES, ids=[f"{r}:{b}" for r, b, _ in CASES])
def test_first_click_after_load_is_never_lost(reset, page, route, button, effect):
    reset(m.fixture())
    log_in(page)
    lost = 0
    for _ in range(N):
        page.goto(route)
        page.wait_for_selector(sel(button))
        page.click(sel(button))
        try:
            page.wait_for_selector(sel(effect), timeout=2_000)
        except Exception:
            lost += 1
    assert lost == 0, f"{lost} of {N} first clicks on {button} at {route} did nothing"


def test_first_click_on_login_and_signup_is_never_lost(reset, page):
    reset(m.fixture())
    lost = 0
    for route, button in (("/login", "login-submit"), ("/signup", "signup-submit")):
        for _ in range(N):
            page.goto(route)
            page.wait_for_selector(sel(button))
            page.click(sel(button))
            try:
                page.wait_for_selector(sel("auth-error"), timeout=2_000)
            except Exception:
                lost += 1
    assert lost == 0, f"{lost} first clicks on login/signup did nothing"


def test_typing_right_after_load_is_not_wiped_by_a_late_render(reset, page):
    """A late re-render must not clear what the user already typed."""
    reset(m.fixture())
    log_in(page)
    wiped = 0
    for _ in range(N):
        page.goto("/")
        page.wait_for_selector(sel("pay-handle"))
        page.fill(sel("pay-handle"), "bob")
        page.fill(sel("pay-amount"), "1.00")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(150)
        if page.input_value(sel("pay-handle")) != "bob" or page.input_value(sel("pay-amount")) != "1.00":
            wiped += 1
    assert wiped == 0, f"{wiped} of {N} loads wiped the typed values"


def _delay_wallet(page, ms=1_500):
    def slow(route):
        page.wait_for_timeout(ms)
        route.continue_()
    page.route(lambda url: url.split("?")[0].endswith("/me") and "?" not in url, slow)


def test_history_actions_wait_for_a_slow_wallet_instead_of_dropping_the_click(reset, page):
    """Deterministic version of the lost first click: GET /me is slow, the user clicks at once."""
    reset(m.fixture())
    log_in(page)
    _delay_wallet(page)
    page.goto("/history")
    page.wait_for_selector(sel("history-asof-submit"))
    page.fill(sel("history-asof-time"), "2025-01-15T09:30")
    page.click(sel("history-asof-submit"))
    page.wait_for_selector(sel("history-asof-balance"), timeout=8_000)
    page.click(sel("history-statement-submit"))
    page.wait_for_selector(sel("history-opening"), timeout=8_000)


def test_history_statement_click_with_a_slow_wallet_is_not_dropped(reset, page):
    reset(m.fixture())
    log_in(page)
    _delay_wallet(page)
    page.goto("/history")
    page.wait_for_selector(sel("history-statement-submit"))
    page.click(sel("history-statement-submit"))
    page.wait_for_selector(sel("history-opening"), timeout=8_000)


@pytest.mark.parametrize("route,button,effect", CASES[:5], ids=[f"{r}:{b}" for r, b, _ in CASES[:5]])
def test_other_screens_do_not_drop_a_click_while_the_wallet_loads(reset, page, route, button, effect):
    reset(m.fixture())
    log_in(page)
    _delay_wallet(page, 1_200)
    page.goto(route)
    page.wait_for_selector(sel(button))
    page.click(sel(button))
    page.wait_for_selector(sel(effect), timeout=8_000)


# ---- stray text in error output ------------------------------------------------------------------

def _stray(page) -> list[str]:
    page.wait_for_timeout(400)
    return page.evaluate("""() => { const out = []; const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        while (w.nextNode()) { const t = w.currentNode.textContent.trim();
          if (/^(undefined|null|false|NaN|\\[object Object\\])$/.test(t) || /\\bundefined\\b|\\[object Object\\]|\\bNaN\\b/.test(t)) out.push(t.slice(0, 60)); }
        return out; }""")


def test_history_errors_show_only_the_message(reset, page):
    reset(m.fixture())
    log_in(page)
    page.goto("/history")
    page.wait_for_selector(sel("history-statement-submit"))
    page.wait_for_load_state("networkidle")
    page.fill(sel("history-from"), "2026-10-05T12:00")
    page.fill(sel("history-to"), "2026-10-01T12:00")
    page.click(sel("history-statement-submit"))
    page.wait_for_selector(sel("history-error"))
    assert _stray(page) == [], _stray(page)
    page.fill(sel("history-asof-time"), "")
    page.click(sel("history-asof-submit"))
    page.wait_for_selector(sel("history-asof-error"))
    assert _stray(page) == [], _stray(page)


@pytest.mark.parametrize("route,button,effect", CASES[:5], ids=[f"{r}:{b}" for r, b, _ in CASES[:5]])
def test_no_screen_leaks_undefined_into_its_error_output(reset, page, route, button, effect):
    reset(m.fixture())
    log_in(page)
    page.goto(route)
    page.wait_for_selector(sel(button))
    page.wait_for_load_state("networkidle")
    page.click(sel(button))
    page.wait_for_selector(sel(effect))
    assert _stray(page) == [], _stray(page)
