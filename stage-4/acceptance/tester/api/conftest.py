"""Stage-3 API fixtures: seeded-history worlds."""
import pytest

import pf_model as m
from hist import fx_history


@pytest.fixture
def hw(make_world):
    """Seeded history world (ada 10000, bob 2500, cy 500 at the end)."""
    w = make_world(fx_history())
    w.open = m.openings(w.fx)
    return w


def fx_tight():
    """cy -> ada 1000 @-5d; ada -> bob 900 @-3d; bob -> ada 900 @-1d. Ending ada 1000, bob 0, cy 0."""
    from hist import at
    return m.history_fixture([
        m.seeded_payment("q1", "cy", "ada", 1_000, at(5)),
        m.seeded_payment("q2", "ada", "bob", 900, at(3)),
        m.seeded_payment("q3", "bob", "ada", 900, at(1))],
        ending={"ada": 1_000, "bob": 0, "cy": 0})


@pytest.fixture
def tw(make_world):
    w = make_world(fx_tight())
    assert m.openings(w.fx) == {"ada": 0, "bob": 0, "cy": 1_000}
    return w


