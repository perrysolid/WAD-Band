"""Tester-seat acceptance suite for pocketful stage 2.

One command per suite, against any build:

    BASE_URL=http://127.0.0.1:8080 python -m pytest stage-2/acceptance/tester/api
    BASE_URL=http://127.0.0.1:8080 python -m pytest stage-2/acceptance/tester/ui
    BASE_URL=http://127.0.0.1:8080 PREVIOUS_BASE_URL=http://127.0.0.1:8081 \
        [SECOND_BASE_URL=http://127.0.0.1:8082] python -m pytest stage-2/acceptance/tester/upgrade

`--base-url` / `--previous-base-url` work instead of the environment variables. The API
suite needs pytest + httpx; the UI and upgrade suites also need playwright + chromium.
"""
from __future__ import annotations

import os
import pathlib
import sys
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from pf_client import CONTROL_TIMEOUT, Api, expect  # noqa: E402
import pf_model as m  # noqa: E402


def pytest_addoption(parser):
    for opt, hlp in (("--base-url", "stage-2 service under test"),
                     ("--previous-base-url", "the accepted stage-1 service (upgrade suite)"),
                     ("--second-base-url", "optional second stage-2 service (D38 A->B)")):
        try:
            parser.addoption(opt, default=None, help=hlp)
        except ValueError:  # already registered by another plugin
            pass


@pytest.fixture(scope="session")
def base_url(request) -> str:
    url = (request.config.getoption("--base-url") or os.environ.get("BASE_URL")
           or os.environ.get("POCKETFUL_BASE_URL"))
    if not url:
        pytest.exit("pass --base-url http://host:port (or set BASE_URL)", 2)
    return url.rstrip("/")


@pytest.fixture(scope="session")
def previous_base_url(request) -> str:
    url = (request.config.getoption("--previous-base-url")
           or os.environ.get("PREVIOUS_BASE_URL"))
    if not url:
        pytest.fail("the upgrade suite needs the stage-1 service: set PREVIOUS_BASE_URL "
                    "(or --previous-base-url)")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def second_base_url(request) -> str | None:
    """Optional: a second, separate stage-2 service for the D38 A -> B upgrade variant."""
    url = request.config.getoption("--second-base-url") or os.environ.get("SECOND_BASE_URL")
    return url.rstrip("/") if url else None


@pytest.fixture
def api(base_url):
    made: list[Api] = []

    def _api(token: str | None = None, url: str | None = None) -> Api:
        client = Api(url or base_url, token=token)
        made.append(client)
        return client

    yield _api
    for c in made:
        c.close()


@pytest.fixture
def control(base_url):
    """Unauthenticated test-control calls with the 10 s budget."""
    client = httpx.Client(base_url=base_url, timeout=CONTROL_TIMEOUT)
    yield client
    client.close()


@pytest.fixture
def reset(control):
    def _reset(fx: dict, *, raw: bool = False) -> httpx.Response:
        resp = control.post("/_test/reset", json=fx)
        if not raw:
            expect(resp, 204)
        return resp
    return _reset


class World:
    def __init__(self, fx: dict, api_factory):
        self.fx = fx
        self.total = m.total(fx)
        self.seeded = {u["handle"]: u["balance"] for u in fx["users"]}
        self.seeded_auths = {a["id"] for a in fx.get("authorizations") or []}
        self._api = api_factory
        self.clients: dict[str, Api] = {}
        for u in fx["users"]:
            self.clients[u["handle"]] = api_factory().authenticate(u["email"], u["password"])

    def __getattr__(self, handle: str) -> Api:
        try:
            return self.__dict__["clients"][handle]
        except KeyError:
            raise AttributeError(handle) from None

    def new_client(self, handle: str) -> Api:
        u = next(u for u in self.fx["users"] if u["handle"] == handle)
        return self._api().authenticate(u["email"], u["password"])

    def adopt(self, handle: str, client: Api, seeded: int = 0) -> None:
        """Track a user created after reset (signup) in the oracle."""
        self.clients[handle] = client
        self.seeded[handle] = seeded

    def balances(self) -> dict[str, int]:
        """Totals (`balance`) per handle."""
        return {h: c.balance() for h, c in self.clients.items()}

    def wallets(self) -> dict[str, tuple[int, int, int]]:
        """(total, available, held) per handle, checked for S2-R1 on the way."""
        out = {}
        for h, c in self.clients.items():
            me = c.me()
            check_me(me, h)
            out[h] = (me["total"], me["available"], me["held"])
        return out

    def oracle(self, *, feeds: bool = True, auths: bool = True) -> dict:
        """Recompute the core invariants from the public interface alone.

        - S2 §1 / §1.1: the sum of every wallet `total` equals the seeded total
        - S2 §2 / §1.2: balance == total, available == total - held, and none is negative
        - held == the sum of `remaining_amount` over the caller's open outgoing holds
        - every authorization is self-consistent (captured + remaining <= amount,
          closed => remaining 0, payment_id is the last of payment_ids, never `open`
          after its expires_at) and is listed only to its two parties
        - every payment a user is party to explains their total exactly, every capture
          in the feed adds up to its authorization's captured_amount, and no payment id
          appears twice in any feed; every feed item obeys the §4 visibility rule
        """
        w = self.wallets()
        assert sum(t for t, _, _ in w.values()) == self.total, \
            f"money created or destroyed: {sum(t for t, _, _ in w.values())} != seeded " \
            f"{self.total} ({w})"
        listed: dict[str, dict] = {}
        if auths:
            for h, c in self.clients.items():
                mine = c.auths()
                held = 0
                for a in mine:
                    check_auth(a, seeded=a["authorization_id"] in self.seeded_auths)
                    assert h in (a["from_handle"], a["to_handle"]), \
                        f"{h} sees an authorization of others: {a}"
                    if a["from_handle"] == h and a["status"] == "open":
                        held += a["remaining_amount"]
                    listed[a["authorization_id"]] = a
                assert held == w[h][2], f"{h}: open holds sum to {held} but held is {w[h][2]}"
        if feeds and not self.fx.get("payments"):
            for handle, client in self.clients.items():
                feed = client.feed()
                ids = [p["payment_id"] for p in feed]
                assert len(ids) == len(set(ids)), f"duplicate payment in {handle}'s feed"
                net = self.seeded[handle]
                for p in feed:
                    assert "authorization_id" in p, f"S2-R12: payment lacks authorization_id {p}"
                    mine = handle in (p["from_handle"], p["to_handle"])
                    assert p["visibility"] == "public" or mine, \
                        f"{handle} sees a private payment of others: {p}"
                    if p["to_handle"] == handle:
                        net += p["amount"]
                    if p["from_handle"] == handle:
                        net -= p["amount"]
                assert net == w[handle][0], \
                    f"{handle}: receipts explain {net} but total is {w[handle][0]}"
                for aid, a in listed.items():
                    if a["from_handle"] != handle or aid in self.seeded_auths:
                        continue
                    caps = [p for p in feed if p["authorization_id"] == aid]
                    assert sum(p["amount"] for p in caps) == a["captured_amount"], \
                        f"captures of {aid} in the feed do not add up: {caps} vs {a}"
                    assert sorted(p["payment_id"] for p in caps) == sorted(a["payment_ids"]), \
                        f"payment_ids of {aid} differ from its capture payments"
        return w


def check_me(me: dict, handle: str | None = None) -> None:
    """S2-R1 on one GET /me body."""
    for k in ("balance", "total", "available", "held"):
        assert isinstance(me.get(k), int) and not isinstance(me.get(k), bool), (k, me)
    assert me["balance"] == me["total"], f"balance must equal total: {me}"
    assert me["available"] == me["total"] - me["held"], f"available != total - held: {me}"
    assert me["available"] >= 0 and me["held"] >= 0, f"negative available/held: {me}"
    if handle:
        assert me["handle"] == handle


AUTH_FIELDS = {"authorization_id", "from_user_id", "from_handle", "to_user_id", "to_handle",
               "amount", "captured_amount", "remaining_amount", "currency", "note",
               "visibility", "status", "expires_at", "payment_id", "payment_ids", "created_at"}


def check_auth(a: dict, *, seeded: bool = False) -> None:
    """S2-R3/R5/R9 self-consistency of one authorization body."""
    assert AUTH_FIELDS <= set(a), f"missing {AUTH_FIELDS - set(a)} in {a}"
    assert a["status"] in ("open", "captured", "voided", "expired"), a
    assert 0 <= a["captured_amount"] <= a["amount"], a
    if a["status"] == "open":
        assert a["remaining_amount"] == a["amount"] - a["captured_amount"], a
        assert m.parse(a["expires_at"]) > m.now() - timedelta(seconds=2), \
            f"S2-R9: still open after its expires_at: {a}"
    else:
        assert a["remaining_amount"] == 0, f"closed but remaining_amount != 0: {a}"
    if not seeded:
        assert isinstance(a["payment_ids"], list), a
        assert a["payment_id"] == (a["payment_ids"][-1] if a["payment_ids"] else None), a
        assert (a["captured_amount"] > 0) == bool(a["payment_ids"]), a
        assert len(set(a["payment_ids"])) == len(a["payment_ids"]), a
    m.assert_d35(a["expires_at"])
    m.assert_d35(a["created_at"])


@pytest.fixture
def make_world(reset, api):
    def _make(fx: dict | None = None) -> World:
        fx = fx if fx is not None else m.fixture()
        reset(fx)
        return World(fx, api)
    return _make


@pytest.fixture
def world(make_world) -> World:
    """Ada 10000, Bob 2500, Cy 500 (EUR, 2 minor units), all signed in."""
    return make_world()


@pytest.fixture
def op_world(make_world) -> World:
    """As `world`, plus Dee (0); Ada is the only settlement operator."""
    return make_world(m.fixture([m.ADA, m.BOB, m.CY, m.user("dee", 0)], operators=["u_ada"]))


# ---- browser (UI and upgrade suites) -------------------------------------------

def _browser_args(url: str) -> list[str]:
    """Treat the service origin as a secure context, as the grading harness does."""
    parts = urlsplit(url)
    return [f"--unsafely-treat-insecure-origin-as-secure={parts.scheme}://{parts.netloc}"]


@pytest.fixture(scope="session")
def browser(base_url):
    from playwright import sync_api as playwright
    with playwright.sync_playwright() as driver:
        instance = driver.chromium.launch(channel="chromium", args=_browser_args(base_url))
        yield instance
        instance.close()


@pytest.fixture
def new_page(browser, base_url):
    contexts = []

    def _new(**kw):
        context = browser.new_context(base_url=base_url, **kw)
        context.set_default_timeout(10_000)
        contexts.append(context)
        return context.new_page()

    yield _new
    for c in contexts:
        c.close()


@pytest.fixture
def page(new_page):
    """A fresh browser context per test, so no session leaks between them."""
    return new_page()
