"""drift — the live-vs-sim monitor, and the two traps it is shaped to prevent.

Guard tests. The re-baselining and horizon-mismatch cases below are the reason this
module has the API it has; if they ever go green for the wrong reason, the monitor has
quietly become incapable of detecting drift.
"""
import inspect

import numpy as np
import pytest

from crucible_stack.orchestrate import (
    DriftEnvelope,
    DriftVerdict,
    build_envelope,
    check_drift,
    envelope_from_r,
)


def _healthy_r(n=60, seed=3):
    """A validated edge: positive expectancy, ordinary noise."""
    return np.random.default_rng(seed).normal(0.3, 1.0, n)


def _env(n=60, seed=3, **kw):
    return envelope_from_r(_healthy_r(n, seed), n_sims=400, seed=1, **kw)


# --- trap 1: the envelope must be frozen ---------------------------------------------

def test_envelope_arrays_are_read_only():
    env = _env()
    with pytest.raises(ValueError):
        env.cum_r[0, 0] = 999.0
    with pytest.raises(ValueError):
        env.max_dd[0] = 999.0


def test_envelope_does_not_alias_caller_arrays():
    paths = np.array([[1.0, 1.0], [2.0, 2.0]])
    env = build_envelope(paths, levels=(50.0,))
    paths[:] = -99.0                                  # mutating the source must not matter
    assert env.cum_r[0, 0] == pytest.approx(1.5)


def test_check_drift_cannot_rebuild_the_envelope():
    """Structural: no parameter here could re-derive a band from current data."""
    params = set(inspect.signature(check_drift).parameters)
    assert params == {"envelope", "realized_r", "breach_level"}
    for rebuildable in ("r", "block", "n_sims", "seed", "paths", "trades"):
        assert rebuildable not in params, f"check_drift grew a {rebuildable} parameter"


def test_rebuilding_the_envelope_would_hide_real_drift():
    """The bug this module exists to prevent, demonstrated rather than described.

    A frozen envelope catches a collapsed book. An envelope recomputed from that same
    collapsed data re-baselines onto it and reports everything as fine.
    """
    frozen = _env()
    collapsed = np.full(24, -1.2)

    assert check_drift(frozen, collapsed).drifted is True          # frozen: caught

    rebaselined = envelope_from_r(collapsed, n_sims=400, seed=1)   # the mistake
    assert check_drift(rebaselined, collapsed).drifted is False    # re-fit: invisible


# --- trap 2: compare at the elapsed period, not the terminal band ---------------------

def test_young_book_is_judged_at_its_own_elapsed_period():
    env = _env()
    live = np.array([0.3, 0.2, 0.4])                  # 3 periods, perfectly ordinary
    v = check_drift(env, live)
    assert v.drifted is False and v.elapsed == 3
    # ...and it WOULD have looked catastrophic against the terminal band:
    assert live.sum() < env.cum_r[-1, 0], "test no longer exercises the horizon trap"


def test_floor_tracks_elapsed_period_not_a_constant():
    env = _env()
    early = check_drift(env, np.full(5, 0.3)).cum_r_floor
    late = check_drift(env, np.full(50, 0.3)).cum_r_floor
    assert late > early           # a positive-expectancy edge is expected to have earned more


def test_outliving_the_provisioned_horizon_is_an_error_not_a_clamp():
    env = _env(n=30)
    with pytest.raises(ValueError, match="exceeds the envelope"):
        check_drift(env, np.zeros(31))


# --- the core comparison -------------------------------------------------------------

def test_healthy_live_path_is_within_the_envelope():
    env = _env()
    v = check_drift(env, _healthy_r(40, seed=11))
    assert isinstance(v, DriftVerdict)
    assert v.drifted is False and v.breaches == ()


def test_collapsed_book_breaches_on_cumulative_r():
    v = check_drift(_env(), np.full(12, -2.0))
    assert v.drifted is True and "cum_r" in v.breaches
    assert v.cum_r < v.cum_r_floor


def test_deep_trough_breaches_on_drawdown_even_when_it_recovers():
    """Ends fine, but was underwater far past anything the sim predicted."""
    live = np.concatenate([np.full(10, -1.5), np.full(10, 2.0)])
    v = check_drift(_env(), live)
    assert "max_dd" in v.breaches
    assert v.cum_r >= v.cum_r_floor, "recovery should keep cumulative R inside the band"


def test_no_live_periods_yet_is_not_drift():
    v = check_drift(_env(), [])
    assert v.drifted is False and v.elapsed == 0
    assert "nothing to compare" in v.reasons[0]


def test_breach_level_is_selectable():
    env = _env()
    live = _healthy_r(40, seed=11)
    assert check_drift(env, live, breach_level=5.0).cum_r_floor \
        < check_drift(env, live, breach_level=50.0).cum_r_floor
    with pytest.raises(ValueError, match="not in envelope levels"):
        check_drift(env, live, breach_level=42.0)


# --- R is additive, not compounded ---------------------------------------------------

def test_accumulation_is_additive_not_compounded():
    """3R + 2R = 5R. cumprod(1+r) would give 5.0 -> 1*4*3 = 12, a different number."""
    env = build_envelope(np.array([[3.0, 2.0]]), levels=(50.0,))
    assert env.cum_r[0, 0] == pytest.approx(3.0)
    assert env.cum_r[1, 0] == pytest.approx(5.0)


def test_drawdown_is_measured_in_R_not_as_a_ratio():
    env = build_envelope(np.array([[1.0, -3.0, 0.5]]), levels=(50.0,))
    assert env.max_dd[0] == pytest.approx(-3.0)      # peak +1 -> trough -2 = -3R


# --- construction --------------------------------------------------------------------

def test_envelope_is_deterministic_for_a_seed():
    a, b = _env(), _env()
    assert np.array_equal(a.cum_r, b.cum_r) and np.array_equal(a.max_dd, b.max_dd)


def test_meta_records_the_engine_and_accumulation():
    m = _env().meta
    assert m["engine"] == "montecarlo.block_index"
    assert m["accumulation"] == "additive_R"
    assert m["n_sims"] == 400


def test_envelope_shape_and_level_validation():
    with pytest.raises(ValueError, match="ascending"):
        DriftEnvelope(levels=(50.0, 5.0), cum_r=np.zeros((2, 2)), max_dd=np.zeros(2))
    with pytest.raises(ValueError, match="cum_r must be"):
        DriftEnvelope(levels=(5.0,), cum_r=np.zeros((2, 3)), max_dd=np.zeros(1))
    with pytest.raises(ValueError, match="non-empty"):
        build_envelope(np.array([]))
