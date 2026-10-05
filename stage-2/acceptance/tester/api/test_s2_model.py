"""Seeded random operation sequences against the reference model (pf_model.Model):
every status/code, every wallet (total, available, held) and every hold's state must match."""
from __future__ import annotations

import random

import pytest

import pf_model as m
from pf_client import code_of


def _result(resp):
    return resp.status_code, (code_of(resp) if resp.status_code >= 400 else None)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_random_sequences_match_the_model(make_world, seed):
    rng = random.Random(seed)
    handles = ["ada", "bob", "cy", "dee"]
    users = [m.user(h, rng.choice([0, 100, 400, 1_500])) for h in handles]
    w = make_world(m.fixture(users))
    model = m.Model(w.fx)
    for step in range(70):
        op = rng.choice(["pay", "auth", "auth", "cap", "cap", "void", "ask", "payrq", "cancel"])
        a, b = rng.choice(handles), rng.choice(handles + ["nobody"])
        amt = rng.choice([1, 25, 90, 250, 700])
        ctx = f"seed {seed} step {step}: {op} {a}->{b} {amt}"
        if op == "pay":
            want = model.pay(a, b, amt)
            got = _result(w.clients[a].pay(b, amt))
        elif op == "auth":
            want = model.authorize(a, b, amt)
            resp = w.clients[a].authorize(b, amt)
            got = _result(resp)
            if got[0] == 201:
                model.add_auth(resp.json()["authorization_id"], a, b, amt)
        elif op in ("cap", "void"):
            if not model.auths:
                continue
            aid = rng.choice(sorted(model.auths))
            caller = rng.choice(handles)
            if op == "cap":
                body = rng.choice([{}, {"amount": amt}, {"amount": amt, "final": False},
                                   {"final": False}, {"amount": amt, "final": True}])
                want = model.capture(caller, aid, body.get("amount"), body.get("final", True))
                got = _result(w.clients[caller].capture(aid, body))
            else:
                want = model.void(caller, aid)
                got = _result(w.clients[caller].void(aid))
            ctx += f" on {aid} by {caller}"
        elif op == "ask":
            want = model.ask(a, b, amt)
            resp = w.clients[a].ask(b, amt)
            got = _result(resp)
            if got[0] == 201:
                model.add_request(resp.json()["request_id"], a, b, amt)
        elif op in ("payrq", "cancel"):
            if not model.requests:
                continue
            rid = rng.choice(sorted(model.requests))
            caller = rng.choice(handles)
            if op == "payrq":
                want = model.pay_request(caller, rid)
                got = _result(w.clients[caller].pay_request(rid, {}))
            else:
                want = model.cancel(caller, rid)
                got = _result(w.clients[caller].post(f"/requests/{rid}/cancel", json={}))
        assert got == want, ctx
    wallets = w.oracle()
    for h in handles:
        assert wallets[h] == (model.total[h], model.available(h), model.held(h)), h
    for aid, ma in model.auths.items():
        a = w.clients[ma["from"]].auth(aid)
        assert a["status"] == ma["status"], aid
        assert a["captured_amount"] == ma["captured"], aid
        assert len(a["payment_ids"]) == ma["captures"], aid
