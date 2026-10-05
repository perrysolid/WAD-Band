"""Shared stage-3 fixtures: a seeded history with known instants and a reference replay."""
from __future__ import annotations

from datetime import timedelta

import pf_model as m

NOW = m.now().replace(microsecond=0)


def at(days: float = 0, hours: float = 0, minutes: float = 0, off: int = 0) -> str:
    return m.iso(NOW - timedelta(days=days, hours=hours, minutes=minutes), off)


def fx_history(**kw) -> dict:
    """p_1 ada->bob 500 @-5d, p_2 bob->ada 200 @-3d, p_3 cy->bob 100 @-1d (private)."""
    return m.history_fixture([
        m.seeded_payment("p_1", "ada", "bob", 500, at(5), note="coffee"),
        m.seeded_payment("p_2", "bob", "ada", 200, at(3)),
        m.seeded_payment("p_3", "cy", "bob", 100, at(1), visibility="private"),
    ], **kw)


def replay(fx: dict, upto=None, *, strictly_before=None) -> dict[str, int]:
    """Reference balances of the seeded history alone (no API activity)."""
    bal = m.openings(fx)
    id2h = {u["id"]: u["handle"] for u in fx["users"]}
    for p in fx["payments"]:
        t = m.parse(p["created_at"])
        if upto is not None and t > upto:
            continue
        if strictly_before is not None and t >= strictly_before:
            continue
        bal[id2h[p["from_user_id"]]] -= p["amount"]
        bal[id2h[p["to_user_id"]]] += p["amount"]
    return bal
