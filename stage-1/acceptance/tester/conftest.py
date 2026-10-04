"""Tester-seat acceptance suite for pocketful stage 1.

Run against any build with one command:

    python -m pytest stage-1/acceptance/tester --base-url http://127.0.0.1:8080

(`BASE_URL=...` works instead of `--base-url`). Needs only pytest and httpx.
"""
from __future__ import annotations

import os
import pathlib
import sys

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from pf_client import CONTROL_TIMEOUT, Api, expect  # noqa: E402
import pf_model as m  # noqa: E402


def pytest_addoption(parser):
    try:
        parser.addoption("--base-url", default=None, help="service under test")
    except ValueError:  # already registered by another plugin
        pass


@pytest.fixture(scope="session")
def base_url(request) -> str:
    url = (request.config.getoption("--base-url") or os.environ.get("BASE_URL")
           or os.environ.get("POCKETFUL_BASE_URL"))
    if not url:
        pytest.exit("pass --base-url http://host:port (or set BASE_URL)", 2)
    return url.rstrip("/")


@pytest.fixture
def api(base_url):
    made: list[Api] = []

    def _api(token: str | None = None) -> Api:
        client = Api(base_url, token=token)
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

    def balances(self) -> dict[str, int]:
        return {h: c.balance() for h, c in self.clients.items()}

    def oracle(self, *, feeds: bool = True) -> dict[str, int]:
        """Recompute the core invariants from the public interface alone.

        - §1.1 the sum of all wallets equals the seeded total
        - §1.2 no wallet is negative
        - every payment a user is party to explains their balance exactly
          (seeded balance + received - sent), so no money moved without a receipt
          and no receipt exists without money moving
        - no payment id appears twice in any feed, and every feed item obeys §4
        """
        bal = self.balances()
        assert all(v >= 0 for v in bal.values()), f"negative balance: {bal}"
        assert sum(bal.values()) == self.total, \
            f"money created or destroyed: {sum(bal.values())} != seeded {self.total} ({bal})"
        if feeds and not self.fx.get("payments"):
            for handle, client in self.clients.items():
                feed = client.feed()
                ids = [p["payment_id"] for p in feed]
                assert len(ids) == len(set(ids)), f"duplicate payment in {handle}'s feed"
                net = self.seeded[handle]
                for p in feed:
                    mine = handle in (p["from_handle"], p["to_handle"])
                    assert p["visibility"] == "public" or mine, \
                        f"{handle} sees a private payment of others: {p}"
                    if p["to_handle"] == handle:
                        net += p["amount"]
                    if p["from_handle"] == handle:
                        net -= p["amount"]
                assert net == bal[handle], \
                    f"{handle}: receipts explain {net} but balance is {bal[handle]}"
        return bal


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
