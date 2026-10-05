"""Browser helpers for the stage-2 UI and upgrade suites.

Elements are found by `data-testid` only. Assertions about money use the D27 format.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

from playwright.sync_api import expect as pw_expect

import pf_model as m

PASSWORD = m.PASSWORD


def sel(name: str) -> str:
    return f"[data-testid='{name}']"


def log_in(page, email: str = "ada@example.com", password: str = PASSWORD,
           expect_success: bool = True) -> None:
    page.goto("/login")
    page.fill(sel("login-email"), email)
    page.fill(sel("login-password"), password)
    page.click(sel("login-submit"))
    if expect_success:
        page.wait_for_selector(sel("current-user"))


def text(page, testid: str) -> str:
    return page.text_content(sel(testid)) or ""


def amount_attr(page, testid: str) -> str | None:
    return page.get_attribute(sel(testid), "data-amount")


def wait_amount(page, testid: str, minor: int) -> None:
    pw_expect(page.locator(sel(testid))).to_have_attribute("data-amount", str(minor))


def wait_wallet(page, total: int, available: int | None = None, held: int = 0,
                mu: int = 2, cur: str = "EUR") -> None:
    """Wait for the wallet panel to show exactly these numbers (S2-U3, D27)."""
    available = total - held if available is None else available
    wait_amount(page, "wallet-balance", total)
    wait_amount(page, "wallet-available", available)
    pw_expect(page.locator(sel("wallet-balance"))).to_have_text(m.money(total, mu, cur))
    pw_expect(page.locator(sel("wallet-available"))).to_have_text(m.money(available, mu, cur))
    if held:
        wait_amount(page, "wallet-held", held)
        pw_expect(page.locator(sel("wallet-held"))).to_have_text(m.money(held, mu, cur))
    else:
        pw_expect(page.locator(sel("wallet-held"))).to_have_count(0)


def fill_pay(page, handle="bob", amount="15.00", note=None, visibility=None) -> None:
    page.wait_for_selector(sel("pay-submit"))
    page.fill(sel("pay-handle"), handle)
    page.fill(sel("pay-amount"), amount)
    if note is not None:
        page.fill(sel("pay-note"), note)
    if visibility:
        page.select_option(sel("pay-visibility"), visibility)


def fill_authorize(page, handle="bob", amount="20.00", note=None, visibility=None) -> None:
    page.wait_for_selector(sel("authorize-submit"))
    page.fill(sel("authorize-handle"), handle)
    page.fill(sel("authorize-amount"), amount)
    if note is not None:
        page.fill(sel("authorize-note"), note)
    if visibility:
        page.select_option(sel("authorize-visibility"), visibility)


def present(page, testid: str) -> bool:
    return page.locator(sel(testid)).count() > 0


def absent(page, testid: str, timeout: float = 5_000) -> None:
    pw_expect(page.locator(sel(testid))).to_have_count(0, timeout=timeout)


def settle(page, ms: int = 600) -> None:
    """Let any in-flight request and re-render finish before a negative assertion."""
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(ms)


class Writes:
    """Records every non-GET request the page sends (method, path, headers, body)."""

    def __init__(self, page):
        self.items: list[dict] = []
        page.on("request", self._on)

    def _on(self, req) -> None:
        if req.method in ("GET", "HEAD", "OPTIONS"):
            return
        body = req.post_data
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            parsed = body
        self.items.append({"method": req.method, "path": urlsplit(req.url).path,
                           "headers": {k.lower(): v for k, v in req.headers.items()},
                           "body": parsed})

    def to(self, path_prefix: str) -> list[dict]:
        return [w for w in self.items if w["path"].startswith(path_prefix)]

    def money_writes(self) -> list[dict]:
        return [w for w in self.items if not w["path"].startswith("/auth/")]


def is_api_call(request, origin: str) -> bool:
    """A fetch/XHR to the service (not a document, script, style, font or image)."""
    return request.resource_type in ("fetch", "xhr") and request.url.startswith(origin)
