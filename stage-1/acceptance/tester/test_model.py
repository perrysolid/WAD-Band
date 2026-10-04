"""Random operation sequences compared against a reference model written from the spec."""
from __future__ import annotations

import random

import pytest

import pf_model as m
from pf_client import code_of, describe


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_random_sequences_match_the_reference_model(make_world, seed):
    """[§1, §4, §8, §9] statuses, codes, balances and request states agree with the model."""
    rng = random.Random(seed)
    users = [m.user(h, rng.choice([0, 50, 300, 1000])) for h in ("ada", "bob", "cy", "dee")]
    w = make_world(m.fixture(users))
    model = m.Model(w.fx)
    handles = [u["handle"] for u in users]
    rids: list[str] = []

    for step in range(150):
        op = rng.choice(["pay", "pay", "ask", "payrq", "payrq", "decline", "cancel", "split"])
        a = rng.choice(handles)
        c = w.clients[a]
        if op == "pay":
            b = rng.choice(handles + ["ghost"])
            amt = rng.randint(1, 400)
            resp = c.pay(b, amt, visibility=rng.choice(["public", "private"]))
            want = model.pay(a, b, amt) if b != "ghost" else (404, "not_found")
        elif op == "ask":
            b = rng.choice(handles)
            amt = rng.randint(1, 600)
            resp = c.ask(b, amt)
            want = model.ask(a, b, amt)
            if resp.status_code == 201:
                rid = resp.json()["request_id"]
                model.add_request(rid, a, b, amt)
                rids.append(rid)
        elif op == "split":
            parts = rng.sample(handles, rng.randint(1, 4))
            amt = rng.randint(1, 500)
            resp = c.split(amt, parts)
            want = (201, None)
            if resp.status_code == 201:
                body = resp.json()
                assert [s["amount"] for s in body["shares"]] == m.equal_split(amt, len(parts))
                for r in body["requests"]:
                    model.add_request(r["request_id"], a, r["payer_handle"], r["amount"])
                    rids.append(r["request_id"])
        else:
            if not rids:
                continue
            rid = rng.choice(rids)
            if op == "payrq":
                resp = c.pay_request(rid)
                want = model.pay_request(a, rid)
            else:
                resp = c.post(f"/requests/{rid}/{op}", json={})
                want = getattr(model, op)(a, rid)
        got = (resp.status_code, code_of(resp) if resp.status_code >= 400 else None)
        assert got == want, f"step {step} {op} by {a}: model {want}, service {got}. {describe(resp)}"

    assert w.balances() == model.balance
    for h, client in w.clients.items():
        for r in client.requests_list():
            assert r["status"] == model.requests[r["request_id"]]["status"], r
    w.oracle()
