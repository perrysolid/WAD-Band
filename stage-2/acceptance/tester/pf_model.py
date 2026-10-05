"""Fixture builders, time helpers, the §9 split rule, bursts and a reference model.

Everything here is written from stage-1.md, stage-2.md and the PLAN (S2-R*, S2-U*, D25-D37).
"""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
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

MINOR_UNITS = {"EUR": 2, "JPY": 0, "BHD": 3}


def fixture(users: list[dict] | None = None, *, currency: str = "EUR",
            minor_units: int | None = None, payments: list[dict] | None = None,
            requests: list[dict] | None = None, operators: list[str] | None = None,
            authorizations: list[dict] | None = None, ttl: object = ...) -> dict:
    fx = {"currency": currency,
          "minor_units": MINOR_UNITS[currency] if minor_units is None else minor_units,
          "users": [dict(u) for u in (users if users is not None else [ADA, BOB, CY])],
          "payments": payments or [], "requests": requests or []}
    if operators is not None:
        fx["settlement_operator_ids"] = operators
    if authorizations is not None:
        fx["authorizations"] = authorizations
    if ttl is not ...:
        fx["authorization_ttl_seconds"] = ttl
    return fx


def hold(aid: str, frm: str, to: str, amount: int, *, status: str = "open",
         expires_at: object = ..., note: str = "", visibility: str = "public",
         **extra) -> dict:
    """A seeded authorization; `frm`/`to` are handles (ids are u_<handle>)."""
    return {"id": aid, "from_user_id": f"u_{frm}", "to_user_id": f"u_{to}", "amount": amount,
            "note": note, "visibility": visibility, "status": status,
            "expires_at": iso(now() + timedelta(hours=2)) if expires_at is ... else expires_at,
            **extra}


def total(fx: dict) -> int:
    return sum(u["balance"] for u in fx["users"])


# ---- time -------------------------------------------------------------------

D35 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3})?\+00:00$")
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(t: datetime, offset_minutes: int = 0) -> str:
    """RFC 3339 for `t` written in the given UTC offset (no fraction)."""
    tz = timezone(timedelta(minutes=offset_minutes))
    return t.astimezone(tz).replace(microsecond=0).isoformat()


def parse(ts: str) -> datetime:
    assert RFC3339.match(ts), f"not RFC 3339 with an offset: {ts!r}"
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def assert_d35(ts: str) -> None:
    """[D35] UTC +00:00; `.mmm` only when the millisecond part is non-zero."""
    assert D35.match(ts), f"D35: expected YYYY-MM-DDTHH:MM:SS[.mmm]+00:00, got {ts!r}"
    assert not ts.endswith(".000+00:00"), f"D35: zero fraction must be omitted: {ts!r}"


# ---- money ------------------------------------------------------------------

def money(minor: int, minor_units: int = 2, currency: str = "EUR") -> str:
    """[D27] `100.00 EUR`; minor_units 0 has no decimal point."""
    assert minor >= 0
    if minor_units == 0:
        return f"{minor} {currency}"
    text = str(minor).rjust(minor_units + 1, "0")
    return f"{text[:-minor_units]}.{text[-minor_units:]} {currency}"


def decimal(minor: int, minor_units: int = 2) -> str:
    """The formatted amount without the currency (capture input prefill)."""
    return money(minor, minor_units, "X")[:-2]


def equal_split(amount: int, n: int) -> list[int]:
    """§9: whole units, differ by at most one, larger shares first."""
    base, rem = divmod(amount, n)
    return [base + (1 if i < rem else 0) for i in range(n)]


# ---- concurrency --------------------------------------------------------------

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


def codes(results) -> dict[tuple, int]:
    out: dict[tuple, int] = {}
    for r in results:
        code = None
        if getattr(r, "status_code", 0) >= 400:
            try:
                code = r.json()["error"]["code"]
            except Exception:
                code = "?"
        k = (getattr(r, "status_code", 0), code)
        out[k] = out.get(k, 0) + 1
    return out


def assert_no_5xx(results) -> None:
    bad = [r for r in results if isinstance(r, Exception) or r.status_code >= 500]
    assert not bad, f"5xx or transport error under load: {tally(results)}; first: " + (
        repr(bad[0]) if isinstance(bad[0], Exception) else bad[0].text[:300])


# ---- reference model --------------------------------------------------------------

class Model:
    """Reference wallet from stage-1 §4/§8 and stage-2 (holds, captures, voids).

    No clock: the model is used with lifetimes far longer than a test run.
    """

    def __init__(self, fx: dict):
        self.total = {u["handle"]: u["balance"] for u in fx["users"]}
        self.requests: dict[str, dict] = {}
        self.auths: dict[str, dict] = {}

    def held(self, h: str) -> int:
        return sum(a["amount"] - a["captured"] for a in self.auths.values()
                   if a["from"] == h and a["status"] == "open")

    def available(self, h: str) -> int:
        return self.total[h] - self.held(h)

    def _move(self, frm: str, to: str, amount: int) -> None:
        self.total[frm] -= amount
        self.total[to] += amount

    def pay(self, frm: str, to: str, amount: int) -> tuple[int, str | None]:
        if frm == to:
            return 422, "self_payment"
        if to not in self.total:
            return 404, "not_found"
        if self.available(frm) < amount:
            return 409, "insufficient_funds"
        self._move(frm, to, amount)
        return 201, None

    def authorize(self, frm: str, to: str, amount: int) -> tuple[int, str | None]:
        if frm == to:
            return 422, "self_payment"
        if to not in self.total:
            return 404, "not_found"
        if self.available(frm) < amount:
            return 409, "insufficient_funds"
        return 201, None

    def add_auth(self, aid: str, frm: str, to: str, amount: int) -> None:
        self.auths[aid] = {"from": frm, "to": to, "amount": amount, "captured": 0,
                           "status": "open", "captures": 0}

    def capture(self, caller: str, aid: str, amount: int | None,
                final: bool = True) -> tuple[int, str | None]:
        a = self.auths[aid]
        if caller != a["to"]:
            return 403, "forbidden"
        if a["status"] != "open":
            return 409, "authorization_not_open"
        remaining = a["amount"] - a["captured"]
        amt = remaining if amount is None else amount
        if amt > remaining:
            return 422, "capture_exceeds_authorization"
        self._move(a["from"], a["to"], amt)
        a["captured"] += amt
        a["captures"] += 1
        if final or a["captured"] == a["amount"]:
            a["status"] = "captured"
        return 201, None

    def void(self, caller: str, aid: str) -> tuple[int, str | None]:
        a = self.auths[aid]
        if caller != a["from"]:
            return 403, "forbidden"
        if a["status"] == "voided":
            return 200, None
        if a["status"] != "open":
            return 409, "authorization_not_open"
        a["status"] = "voided"
        return 200, None

    def ask(self, requester: str, payer: str, amount: int) -> tuple[int, str | None]:
        if requester == payer:
            return 422, "self_request"
        if payer not in self.total:
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
        if self.available(caller) < rq["amount"]:
            return 409, "insufficient_funds"
        self._move(caller, rq["requester"], rq["amount"])
        rq["status"] = "paid"
        return 201, None

    def cancel(self, caller: str, rid: str) -> tuple[int, str | None]:
        rq = self.requests[rid]
        if caller != rq["requester"]:
            return 403, "forbidden"
        if rq["status"] in ("paid", "declined"):
            return 409, "request_not_pending"
        rq["status"] = "cancelled"
        return 200, None
