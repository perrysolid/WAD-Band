"""Fixture builders, the §9 split rule, a concurrency burst, and a reference model.

Everything here is written from stage-1.md alone.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

PASSWORD = "correct horse"


def user(handle: str, balance: int, *, uid: str | None = None, email: str | None = None,
         display_name: str | None = None, password: str = PASSWORD) -> dict:
    return {"id": uid or f"u_{handle}", "email": email or f"{handle}@example.com",
            "password": password, "display_name": display_name or handle.title(),
            "handle": handle, "balance": balance}


ADA = user("ada", 10_000, display_name="Ada")
BOB = user("bob", 2_500, display_name="Bob")
CY = user("cy", 500, display_name="Cy")


def fixture(users: list[dict] | None = None, *, currency: str = "EUR",
            minor_units: int | None = None, payments: list[dict] | None = None,
            requests: list[dict] | None = None, operators: list[str] | None = None) -> dict:
    fx = {"currency": currency,
          "minor_units": {"EUR": 2, "JPY": 0, "BHD": 3}[currency] if minor_units is None
          else minor_units,
          "users": [dict(u) for u in (users if users is not None else [ADA, BOB, CY])],
          "payments": payments or [], "requests": requests or []}
    if operators is not None:
        fx["settlement_operator_ids"] = operators
    return fx


def total(fx: dict) -> int:
    return sum(u["balance"] for u in fx["users"])


def equal_split(amount: int, n: int) -> list[int]:
    """§9: whole units, differ by at most one, larger shares first."""
    base, rem = divmod(amount, n)
    return [base + (1 if i < rem else 0) for i in range(n)]


def burst(fn: Callable[[int], object], n: int, timeout: float = 60.0) -> list:
    """Run fn(i) for i in range(n) with all workers released together (<=50 per wave)."""
    if n > 50:
        out: list = []
        for start in range(0, n, 50):
            out.extend(burst(lambda i, s=start: fn(s + i), min(50, n - start), timeout))
        return out
    gate = threading.Barrier(n)

    def run(i: int):
        try:
            gate.wait(timeout=timeout)
        except threading.BrokenBarrierError:
            pass
        try:
            return fn(i)
        except Exception as exc:  # surfaced, not swallowed
            return exc

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(run, range(n)))


def tally(results) -> dict[int, int]:
    out: dict[int, int] = {}
    for r in results:
        s = getattr(r, "status_code", 0)
        out[s] = out.get(s, 0) + 1
    return dict(sorted(out.items()))


def assert_no_5xx(results) -> None:
    bad = [r for r in results if isinstance(r, Exception) or r.status_code >= 500]
    assert not bad, f"5xx or transport error under load: {tally(results)}; first: " + (
        repr(bad[0]) if isinstance(bad[0], Exception) else bad[0].text[:300])


class Model:
    """A reference wallet model from §4, §8, §9. Balances and request states only."""

    def __init__(self, fx: dict):
        self.balance = {u["handle"]: u["balance"] for u in fx["users"]}
        self.requests: dict[str, dict] = {}

    def pay(self, frm: str, to: str, amount: int) -> tuple[int, str | None]:
        if frm == to:
            return 422, "self_payment"
        if to not in self.balance:
            return 404, "not_found"
        if self.balance[frm] < amount:
            return 409, "insufficient_funds"
        self.balance[frm] -= amount
        self.balance[to] += amount
        return 201, None

    def ask(self, requester: str, payer: str, amount: int) -> tuple[int, str | None]:
        if requester == payer:
            return 422, "self_request"
        if payer not in self.balance:
            return 404, "not_found"
        return 201, None

    def add_request(self, rid: str, requester: str, payer: str, amount: int) -> None:
        self.requests[rid] = {"requester": requester, "payer": payer, "amount": amount,
                              "status": "pending"}

    def pay_request(self, caller: str, rid: str) -> tuple[int, str | None]:
        rq = self.requests[rid]
        if caller != rq["payer"]:
            return 403, "forbidden"
        if rq["status"] != "pending":
            return 409, "request_not_pending"
        if self.balance[caller] < rq["amount"]:
            return 409, "insufficient_funds"
        self.balance[caller] -= rq["amount"]
        self.balance[rq["requester"]] += rq["amount"]
        rq["status"] = "paid"
        return 201, None

    def decline(self, caller: str, rid: str) -> tuple[int, str | None]:
        rq = self.requests[rid]
        if caller != rq["payer"]:
            return 403, "forbidden"
        if rq["status"] in ("paid", "cancelled"):
            return 409, "request_not_pending"
        rq["status"] = "declined"
        return 200, None

    def cancel(self, caller: str, rid: str) -> tuple[int, str | None]:
        rq = self.requests[rid]
        if caller != rq["requester"]:
            return 403, "forbidden"
        if rq["status"] in ("paid", "declined"):
            return 409, "request_not_pending"
        rq["status"] = "cancelled"
        return 200, None
