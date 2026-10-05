"""S2-U1 routes and navigation; S2-U2 signup, login, logout and the signed-in header."""
from __future__ import annotations

import pytest
from playwright.sync_api import expect as pw_expect

import pf_model as m
from pf_client import expect
from ui_kit import absent, log_in, sel, settle, text

SIGNED_IN_ROUTES = [("/", "pay-submit"), ("/requests", "incoming-list"),
                    ("/split", "split-submit"), ("/authorizations", "authorize-submit")]


@pytest.fixture
def seeded(reset):
    fx = m.fixture()
    reset(fx)
    return fx


@pytest.mark.parametrize("route,anchor", SIGNED_IN_ROUTES + [("/signup", "signup-submit"),
                                                           ("/login", "login-submit")])
def test_every_route_loads_directly_by_url(seeded, page, route, anchor):
    """[S2-U1] deep links work, including /login and /signup when already signed in."""
    log_in(page)
    resp = page.goto(route)
    assert resp is None or resp.status < 400, (route, resp.status)
    page.wait_for_selector(sel(anchor), state="attached")


@pytest.mark.parametrize("route,anchor", SIGNED_IN_ROUTES)
def test_current_user_and_handle_on_every_signed_in_screen(seeded, page, route, anchor):
    """[S2-U2] current-user contains the display name; current-handle is exactly the handle."""
    log_in(page)
    page.goto(route)
    page.wait_for_selector(sel(anchor), state="attached")
    pw_expect(page.locator(sel("current-user"))).to_be_visible()
    assert "Ada" in text(page, "current-user")
    assert text(page, "current-handle").strip() == "ada"
    assert "@" not in text(page, "current-handle")


@pytest.mark.parametrize("route", ["/", "/requests", "/split", "/authorizations"])
def test_signed_out_visits_go_to_login(seeded, page, route):
    """[S2-U1] a signed-out visit lands on the login form, never a broken screen."""
    page.goto(route)
    page.wait_for_selector(sel("login-submit"))
    assert page.locator(sel("current-user")).count() == 0


@pytest.mark.parametrize("route,anchor", SIGNED_IN_ROUTES)
def test_navigation_reaches_every_screen(seeded, page, route, anchor):
    """[S2-U1] consistent, visible navigation: each screen links to the other three."""
    log_in(page)
    page.goto(route)
    page.wait_for_selector(sel(anchor), state="attached")
    for target, target_anchor in SIGNED_IN_ROUTES:
        links = page.locator(f"a[href='{target}'], a[href$='{target}']" if target != "/"
                             else "a[href='/']")
        visible = [i for i in range(links.count()) if links.nth(i).is_visible()]
        assert visible, f"no visible link to {target} on {route}"
    # clicking through actually navigates
    page.locator("a[href='/requests'], a[href$='/requests']").first.click()
    page.wait_for_selector(sel("incoming-list"), state="attached")


def test_signup_lands_signed_in_with_derived_handle(seeded, page):
    """[S2-U2, §4] the handle shown is the one derived from the email."""
    page.goto("/signup")
    absent(page, "auth-error")
    page.fill(sel("signup-email"), "Dee.Ann+x@Example.com")
    page.fill(sel("signup-password"), "correct horse")
    page.fill(sel("signup-display-name"), "Dee Ann")
    page.click(sel("signup-submit"))
    page.wait_for_selector(sel("current-user"))
    assert "Dee Ann" in text(page, "current-user")
    assert text(page, "current-handle").strip() == "dee_ann_x"
    page.wait_for_selector(sel("wallet-balance"))
    assert text(page, "wallet-balance").strip() == "0.00 EUR"
    absent(page, "auth-error")


@pytest.mark.parametrize("email,password,why", [
    ("ada@example.com", "correct horse", "email_taken"),
    ("new@example.com", "short", "password < 8"),
    ("not-an-email", "correct horse", "bad email"),
    ("ada@elsewhere.org", "correct horse", "handle_taken"),
])
def test_signup_refusals_show_auth_error(seeded, page, api, email, password, why):
    page.goto("/signup")
    page.fill(sel("signup-email"), email)
    page.fill(sel("signup-password"), password)
    page.fill(sel("signup-display-name"), "X")
    page.click(sel("signup-submit"))
    page.wait_for_selector(sel("auth-error"))
    assert text(page, "auth-error").strip(), why
    assert page.locator(sel("current-user")).count() == 0
    if why == "handle_taken":
        expect(api().login("ada@elsewhere.org"), 401)


def test_bad_login_shows_auth_error_then_success_clears_it(seeded, page):
    page.goto("/login")
    absent(page, "auth-error")
    log_in(page, password="wrong password", expect_success=False)
    page.wait_for_selector(sel("auth-error"))
    assert page.locator(sel("current-user")).count() == 0
    page.fill(sel("login-email"), "ada@example.com")
    page.fill(sel("login-password"), "correct horse")
    page.click(sel("login-submit"))
    page.wait_for_selector(sel("current-user"))
    absent(page, "auth-error")


def test_unknown_email_login_is_an_auth_error(seeded, page):
    log_in(page, email="nobody@example.com", expect_success=False)
    page.wait_for_selector(sel("auth-error"))


def test_logout_signs_out_everywhere_in_the_tab(seeded, page):
    log_in(page)
    page.goto("/requests")
    page.click(sel("logout-button"))
    page.wait_for_selector(sel("current-user"), state="detached")
    page.goto("/")
    page.wait_for_selector(sel("login-submit"))
    assert page.locator(sel("current-user")).count() == 0


def test_signed_in_state_survives_a_reload(seeded, page):
    log_in(page)
    page.reload()
    page.wait_for_selector(sel("current-user"))
    page.goto("/authorizations")
    page.wait_for_selector(sel("current-user"))


def test_a_second_user_in_another_context_is_independent(seeded, page, new_page):
    log_in(page)
    other = new_page()
    log_in(other, email="bob@example.com")
    assert text(other, "current-handle").strip() == "bob"
    page.reload()
    page.wait_for_selector(sel("current-user"))
    assert text(page, "current-handle").strip() == "ada"
    settle(page, 100)
