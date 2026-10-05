"""Payment timestamps, GET /me as_of, GET /statement (stage-3 'Payment timestamps',
'GET /me as an instant', 'GET /statement')."""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

import pytest

import pf_model as m
from pf_client import expect, expect_error
from hist import at, replay


# ---- timestamps and seeding ---------------------------------------------------------------

def test_seeded_balances_are_the_ending_balances(hw):
    """[S3 timestamps] loading the seeded payments does not change `balance`."""
    assert hw.balances() == {"ada": 10_000, "bob": 2_500, "cy": 500}
    hw.oracle()


def test_seeded_created_at_is_served_as_the_same_instant(hw):
    feed = {p["payment_id"]: p for p in hw.ada.feed()}
    for pid, days in (("p_1", 5), ("p_2", 3)):
        assert m.parse(feed[pid]["created_at"]) == m.parse(at(days))
        m.assert_d35(feed[pid]["created_at"])


def test_seeded_created_at_with_an_offset(make_world):
    fx = m.history_fixture([m.seeded_payment("p_1", "ada", "bob", 500, at(2, off=330))])
    w = make_world(fx)
    p = w.ada.feed()[0]
    assert m.parse(p["created_at"]) == m.parse(at(2))


def test_activity_stays_newest_first_by_created_at(hw):
    feed = hw.ada.feed()
    stamps = [m.parse(p["created_at"]) for p in feed]
    assert stamps == sorted(stamps, reverse=True)
    expect(hw.ada.pay("cy", 1), 201)
    top = hw.ada.feed()[0]
    assert top["amount"] == 1 and m.parse(top["created_at"]) > m.parse(at(1))


def test_omitted_created_at_means_reset_time_before_later_payments(make_world):
    before = m.now() - timedelta(seconds=1)
    fx = m.history_fixture([m.seeded_payment("p_1", "ada", "bob", 500)])
    w = make_world(fx)
    p1 = [p for p in w.ada.feed() if p["payment_id"] == "p_1"][0]
    t1 = m.parse(p1["created_at"])
    assert before <= t1 <= m.now() + timedelta(seconds=1)
    later = expect(w.ada.pay("bob", 7), 201).json()
    assert m.parse(later["created_at"]) >= t1
    entries = w.ada.statement_all()["entries"]
    assert [e["payment"]["payment_id"] for e in entries][0] == "p_1"
    assert entries[0]["payment"]["amount"] == 500


def test_future_created_at_is_422_and_changes_nothing(make_world, reset):
    w = make_world(m.fixture())
    expect(w.ada.pay("bob", 11), 201)
    before = w.balances()
    fx = m.history_fixture([m.seeded_payment("p_1", "ada", "bob", 500,
                                             m.iso(m.now() + timedelta(hours=1)))])
    expect_error(reset(fx, raw=True), 422, "validation_failed")
    assert w.balances() == before        # the old state (and its tokens) still stands
    assert len(w.ada.feed()) == 1


def test_seeded_payment_one_second_in_the_past_is_fine(make_world):
    make_world(m.history_fixture([m.seeded_payment("p_1", "ada", "bob", 5, at(0, 0, 0))]))


@pytest.mark.parametrize("bad", ["2026-09-24", "2026-09-24T10:00:00", "", "yesterday", 5, None])
def test_invalid_seeded_created_at_is_422(reset, bad):
    fx = m.history_fixture([m.seeded_payment("p_1", "ada", "bob", 5, bad)])
    expect_error(reset(fx, raw=True), 422, "validation_failed")


def test_every_endpoint_returning_a_payment_has_created_at(op_world):
    w = op_world
    p = expect(w.ada.pay("bob", 5), 201).json()
    a = expect(w.ada.authorize("bob", 5), 201).json()
    c = expect(w.bob.capture(a["authorization_id"]), 201).json()
    s = expect(w.ada.settle([{"from_handle": "ada", "to_handle": "cy", "amount": 1}]), 201).json()
    r = expect(w.bob.ask("ada", 3), 201).json()
    rp = expect(w.ada.pay_request(r["request_id"]), 201).json()
    for pay in (p, c, rp, *s["payments"], *w.ada.feed()):
        m.assert_d35(pay["created_at"])


# ---- GET /me?as_of ---------------------------------------------------------------------------

def _enc(s: str) -> str:
    return quote(s, safe="")


def test_me_without_temporal_params_is_unchanged(hw):
    me = hw.ada.me()
    assert "as_of" not in me and "known_at" not in me
    assert me["balance"] == 10_000 == me["total"]


def test_as_of_between_payments(hw):
    for days_ago, expected_t in ((6, None), (4, 5), (2, 3), (0.5, 1)):
        t = at(days_ago)
        me = hw.ada.me_at(as_of=t)
        want = replay(hw.fx, upto=m.parse(t))
        assert me["balance"] == me["total"] == want["ada"], (days_ago, me)
        assert me["available"] == me["balance"] and me["held"] == 0
        assert me["as_of"] == t, "as_of is echoed exactly"
    hw.oracle()


def test_as_of_before_the_earliest_payment_is_the_opening_balance(hw):
    for who in ("ada", "bob", "cy"):
        me = hw.clients[who].me_at(as_of=at(30))
        assert me["balance"] == hw.open[who]
    assert hw.ada.me_at(as_of="1970-01-01T00:00:00+00:00")["balance"] == hw.open["ada"]


def test_as_of_is_inclusive_at_exactly_the_payment_instant(hw):
    t = at(3)                                              # p_2 bob -> ada 200
    after = replay(hw.fx, upto=m.parse(t))
    before = replay(hw.fx, strictly_before=m.parse(t))
    assert after["ada"] == before["ada"] + 200
    assert hw.ada.me_at(as_of=t)["balance"] == after["ada"]
    just_before = m.iso(m.parse(t) - timedelta(seconds=1))
    assert hw.ada.me_at(as_of=just_before)["balance"] == before["ada"]


def test_as_of_at_or_after_the_latest_payment_is_the_current_balance(hw):
    expect(hw.ada.pay("bob", 123), 201)
    cur = hw.ada.me()["balance"]
    far = "2999-01-01T00:00:00+00:00"
    assert hw.ada.me_at(as_of=far)["balance"] == cur
    last = hw.ada.feed()[0]["created_at"]
    assert hw.ada.me_at(as_of=last)["balance"] == cur


def test_as_of_accepts_any_offset_and_echoes_it_verbatim(hw):
    t = at(3)
    for off in (0, 120, -300, 330):
        s = m.iso(m.parse(t), off)
        me = hw.ada.me_at(as_of=s)
        assert me["as_of"] == s and me["balance"] == replay(hw.fx, upto=m.parse(t))["ada"]
    z = t.replace("+00:00", "Z")
    assert hw.ada.me_at(as_of=z)["as_of"] == z


@pytest.mark.parametrize("bad", ["2026-09-24T13:20:00", "2026-09-24", "", "13:20", "now",
                                 "2026-09-24T13:20:00+0000", "2026-13-40T00:00:00+00:00",
                                 "1e9", "0", "2026-09-24 13:20:00+00:00", "T", "  "])
def test_bad_as_of_is_422(hw, bad):
    expect_error(hw.ada.get("/me", params={"as_of": bad}), 422, "validation_failed")
    expect_error(hw.ada.get("/me", params={"known_at": bad}), 422, "validation_failed")


def test_a_raw_unencoded_plus_offset_is_not_silently_accepted(hw):
    """'+' in a query string decodes to a space, so the offset is lost: 422 (or, if the
    service reads it as +00:00, the exact echo must still match what was sent)."""
    resp = hw.ada.get("/me?as_of=2026-09-24T13:20:00+00:00")
    if resp.status_code == 200:
        assert resp.json()["as_of"] in ("2026-09-24T13:20:00+00:00", "2026-09-24T13:20:00 00:00")
    else:
        expect_error(resp, 422, "validation_failed")


def test_as_of_needs_a_token(hw, api):
    expect_error(api().get("/me", params={"as_of": at(1)}), 401, "unauthenticated")


def test_opening_balance_of_a_new_account_is_zero(hw, api):
    c = api()
    tok = expect(c.signup("new@example.com", "long password", "New"), 201).json()["token"]
    n = api(tok)
    assert n.me_at(as_of="1999-01-01T00:00:00+00:00")["balance"] == 0
    expect(hw.ada.pay("new", 40), 201)
    assert n.me()["balance"] == 40
    assert n.me_at(as_of=at(2))["balance"] == 0


def test_as_of_views_always_sum_to_the_seeded_total(hw):
    expect(hw.ada.pay("bob", 1), 201)
    expect(hw.bob.pay("cy", 2), 201)
    for t in (at(40), at(5), at(4), at(3), at(2), at(1), at(0, 0, 1), "2999-01-01T00:00:00+00:00"):
        s = sum(c.me_at(as_of=t)["balance"] for c in hw.clients.values())
        assert s == hw.total, (t, s)


# ---- GET /statement ----------------------------------------------------------------------------

def test_full_statement_shape_and_arithmetic(hw):
    st = hw.ada.statement()
    assert {"opening_balance", "entries", "closing_balance", "has_more", "snapshot"} <= set(st)
    assert st["opening_balance"] == hw.open["ada"] and st["closing_balance"] == 10_000
    assert [e["payment"]["payment_id"] for e in st["entries"]] == ["p_1", "p_2"]
    assert [e["delta"] for e in st["entries"]] == [-500, 200]
    run = st["opening_balance"]
    for e in st["entries"]:
        run += e["delta"]
        assert e["balance_after"] == run
        assert {"payment", "delta", "balance_after", "revision", "effective_at",
                "recorded_at"} <= set(e)
        assert e["revision"] == 1 and e["payment"]["amount"] == abs(e["delta"])
    assert sum(e["delta"] for e in st["entries"]) + st["opening_balance"] == st["closing_balance"]
    assert st["has_more"] is False
    hw.oracle()


def test_statement_contains_only_my_payments_even_when_others_are_public(hw):
    assert [e["payment"]["payment_id"] for e in hw.cy.statement()["entries"]] == ["p_3"]
    assert [e["delta"] for e in hw.cy.statement()["entries"]] == [-100]
    third = [e["payment"]["payment_id"] for e in hw.bob.statement()["entries"]]
    assert third == ["p_1", "p_2", "p_3"]
    assert [e["delta"] for e in hw.bob.statement()["entries"]] == [500, -200, 100]


def test_statement_includes_private_payments_of_the_owner(hw):
    ent = hw.bob.statement()["entries"]
    assert any(e["payment"]["visibility"] == "private" for e in ent)


def test_statement_window_is_half_open(hw):
    t2, t3 = at(3), at(1)
    st = hw.ada.statement(**{"from": t2})
    assert [e["payment"]["payment_id"] for e in st["entries"]] == ["p_2"]       # from inclusive
    assert st["opening_balance"] == replay(hw.fx, strictly_before=m.parse(t2))["ada"]
    st = hw.ada.statement(**{"to": t2})
    assert [e["payment"]["payment_id"] for e in st["entries"]] == ["p_1"]       # to exclusive
    assert st["closing_balance"] == replay(hw.fx, strictly_before=m.parse(t2))["ada"]
    st = hw.bob.statement(**{"from": at(5), "to": t3})
    assert [e["payment"]["payment_id"] for e in st["entries"]] == ["p_1", "p_2"]
    assert st["opening_balance"] == hw.open["bob"]
    assert st["closing_balance"] == replay(hw.fx, strictly_before=m.parse(t3))["bob"]
    assert st["opening_balance"] + sum(e["delta"] for e in st["entries"]) == st["closing_balance"]


def test_statement_window_edges_exact_instants(hw):
    t = at(5)                                         # p_1 exactly
    inc = hw.ada.statement(**{"from": t, "to": at(5, minutes=-1)})
    assert [e["payment"]["payment_id"] for e in inc["entries"]] == ["p_1"]
    exc = hw.ada.statement(**{"from": at(5, minutes=-1)})
    assert "p_1" not in [e["payment"]["payment_id"] for e in exc["entries"]]
    empty = hw.ada.statement(**{"from": t, "to": t})
    assert empty["entries"] == [] and empty["opening_balance"] == empty["closing_balance"]


def test_statement_empty_window_and_far_future(hw):
    st = hw.ada.statement(**{"from": at(100), "to": at(90)})
    assert st["entries"] == [] and st["opening_balance"] == st["closing_balance"] == hw.open["ada"]
    st = hw.ada.statement(**{"from": "2999-01-01T00:00:00+00:00", "to": "2999-06-01T00:00:00+00:00"})
    assert st["entries"] == [] and st["opening_balance"] == st["closing_balance"] == 10_000


def test_statement_to_defaults_to_now_and_from_to_the_wallet_opening(hw):
    st = hw.ada.statement()
    assert st["opening_balance"] == hw.open["ada"] and st["closing_balance"] == 10_000
    assert hw.ada.statement(**{"from": at(100)})["opening_balance"] == hw.open["ada"]


def test_statement_ties_break_by_payment_id(make_world):
    t = at(2)
    fx = m.history_fixture([m.seeded_payment("p_b", "ada", "bob", 10, t),
                            m.seeded_payment("p_a", "bob", "ada", 5, t),
                            m.seeded_payment("p_c", "ada", "cy", 1, t)])
    w = make_world(fx)
    ids = [e["payment"]["payment_id"] for e in w.ada.statement()["entries"]]
    assert ids == sorted(ids)
    st = w.ada.statement()
    run = st["opening_balance"]
    for e in st["entries"]:
        run += e["delta"]
        assert e["balance_after"] == run
    w.oracle()


@pytest.mark.parametrize("limit,offset", [(1, 0), (1, 1), (2, 0), (2, 2), (3, 0), (50, 0),
                                          (1, 10), (200, 0)])
def test_pagination_never_changes_balances_or_entries(hw, limit, offset):
    full = hw.bob.statement()
    page = hw.bob.statement(limit=limit, offset=offset)
    assert page["opening_balance"] == full["opening_balance"]
    assert page["closing_balance"] == full["closing_balance"]
    assert page["entries"] == full["entries"][offset:offset + limit]
    assert page["has_more"] is (offset + limit < len(full["entries"]))


def test_pagination_over_many_entries(make_world):
    pays = [m.seeded_payment(f"p_{i:03d}", "ada" if i % 2 else "bob", "bob" if i % 2 else "ada",
                             1 + i % 7, at(0, minutes=300 - i)) for i in range(120)]
    w = make_world(m.history_fixture(pays, ending={"ada": 5_000, "bob": 5_000, "cy": 0}))
    full = w.ada.statement(limit=200)
    assert len(full["entries"]) == 120 and full["has_more"] is False
    seen, offset = [], 0
    while True:
        page = w.ada.statement(limit=25, offset=offset)
        assert page["opening_balance"] == full["opening_balance"]
        assert page["closing_balance"] == full["closing_balance"]
        seen.extend(page["entries"])
        if not page["has_more"]:
            break
        offset += 25
    assert seen == full["entries"]
    assert w.ada.statement(limit=25, offset=200)["entries"] == []
    assert w.ada.statement(limit=25, offset=200)["has_more"] is False
    w.oracle()


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"limit": -1}, {"limit": "1e2"},
                                    {"limit": "+4"}, {"limit": "4.0"}, {"offset": -1},
                                    {"offset": "abc"}, {"from": "2026-09-24"}, {"to": ""},
                                    {"from": "2026-09-24T10:00:00"}])
def test_statement_bad_parameters_are_422(hw, params):
    expect_error(hw.ada.get("/statement", params=params), 422, "validation_failed")


def test_statement_unknown_parameters_are_ignored(hw):
    assert hw.ada.statement(zzz="1") ["entries"] == hw.ada.statement()["entries"]


def test_statement_needs_a_token(hw, api):
    expect_error(api().get("/statement"), 401, "unauthenticated")


def test_statement_for_a_user_with_no_payments(make_world):
    w = make_world(m.fixture())
    st = w.ada.statement()
    assert st["entries"] == [] and st["opening_balance"] == st["closing_balance"] == 10_000
    assert st["has_more"] is False and st["snapshot"]


def test_statement_follows_new_api_payments_and_matches_balance(hw):
    for i in range(5):
        expect(hw.ada.pay("bob", 10 + i), 201)
        expect(hw.bob.pay("ada", 3), 201)
    st = hw.ada.statement_all()
    assert st["closing_balance"] == hw.ada.balance()
    assert len(st["entries"]) == 2 + 10
    hw.oracle()


def test_statement_does_not_list_requests_splits_or_open_holds(hw):
    expect(hw.ada.authorize("bob", 50), 201)
    expect(hw.ada.split(30, ["ada", "bob", "cy"]), 201)
    assert [e["payment"]["payment_id"] for e in hw.ada.statement()["entries"]] == ["p_1", "p_2"]
