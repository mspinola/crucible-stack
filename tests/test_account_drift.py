"""account_drift — the currency shell over the capital-free drift core.

The properties worth guarding here are boundary properties: that the decision stays
capital-free, that sizing cannot leak into it, and that a granularity mismatch is refused
rather than silently answered.
"""
import numpy as np
import pandas as pd
import pytest
from crucible.edge import TradeLog

from crucible_stack.orchestrate import (
    DriftVerdict,
    check_account_drift,
    envelope_from_r,
    in_currency,
    monthly_r,
    provision_envelope,
)


def _log(r, *, freq="10D", start="2015-01-05"):
    entry = pd.date_range(start, periods=len(r), freq=freq)
    return TradeLog.from_frame(pd.DataFrame(
        {"r": r, "entry_date": entry, "exit_date": entry + pd.Timedelta(days=6)}))


def _healthy(n=120, seed=5):
    return _log(np.random.default_rng(seed).normal(0.3, 1.0, n))


# --- the aggregation is capital-free and concurrency-correct --------------------------

def test_monthly_r_is_plain_sum_of_R_with_no_capital_assumption():
    # three trades exiting in the same month are ONE monthly move of +6R
    tl = _log([1.0, 2.0, 3.0], freq="2D", start="2015-01-05")
    assert monthly_r(tl) == pytest.approx([6.0])


def test_monthly_r_spans_a_gap_free_grid():
    tl = _log([1.0, 1.0], freq="60D", start="2015-01-15")
    m = monthly_r(tl)
    assert len(m) >= 3 and m.sum() == pytest.approx(2.0)   # empty months are 0R, not dropped


def test_empty_log_aggregates_to_no_periods():
    assert monthly_r(TradeLog.from_frame(
        pd.DataFrame({"r": [], "exit_date": pd.to_datetime([])}))).size == 0


def test_missing_exit_date_is_a_clear_error():
    tl = TradeLog.from_frame(pd.DataFrame({"r": [1.0, -1.0]}))
    with pytest.raises(ValueError, match="exit_date"):
        monthly_r(tl)


# --- the decision must not depend on capital -----------------------------------------

def test_verdict_is_identical_regardless_of_account_size():
    """R is risk-normalized, so drift is an EDGE question, not an account question."""
    env = provision_envelope(_healthy(), n_sims=300, seed=1)
    live = _healthy(40, seed=9)
    a = check_account_drift(env, live)
    # nothing about starting capital, risk_pct or sizing is reachable from this call:
    import inspect
    params = set(inspect.signature(check_account_drift).parameters)
    assert params == {"envelope", "live_log", "breach_level"}
    for capital_ish in ("risk_pct", "sizes", "starting_capital", "commission", "currency"):
        assert capital_ish not in params, f"{capital_ish} leaked into the decision path"
    assert isinstance(a, DriftVerdict)


def test_a_sizes_column_on_the_log_is_ignored():
    """The shell reads `r` only. A per-trade `sizes` column survives onto the frame
    (verified), so this would differ if sizing were consulted."""
    base = _healthy(60, seed=4)
    frame = base.frame.copy()
    frame["sizes"] = 0.001                       # a sizing column the shell must ignore
    assert monthly_r(TradeLog.from_frame(frame)) == pytest.approx(monthly_r(base))


# --- granularity is load-bearing ------------------------------------------------------

def test_envelope_without_a_declared_period_is_refused():
    raw = envelope_from_r(np.random.default_rng(0).normal(0.3, 1.0, 40), n_sims=200)
    assert "period" not in raw.meta
    with pytest.raises(ValueError, match="granularity mismatch"):
        check_account_drift(raw, _healthy(30))


def test_mismatched_period_is_refused_rather_than_answered():
    weekly = envelope_from_r(np.random.default_rng(0).normal(0.1, 0.5, 60),
                             n_sims=200, meta={"period": "W"})
    with pytest.raises(ValueError, match="granularity mismatch"):
        check_account_drift(weekly, _healthy(30))


def test_provisioned_envelope_declares_monthly_and_is_accepted():
    env = provision_envelope(_healthy(), n_sims=300, seed=1)
    assert env.meta["period"] == "M"
    assert check_account_drift(env, _healthy(40, seed=9)).drifted in (True, False)


def test_provisioning_needs_more_than_one_month():
    with pytest.raises(ValueError, match=">= 2 months"):
        provision_envelope(_log([1.0, 2.0], freq="2D"), n_sims=50)


# --- end to end -----------------------------------------------------------------------

def test_healthy_live_book_is_within_its_envelope():
    env = provision_envelope(_healthy(), n_sims=400, seed=1)
    assert check_account_drift(env, _healthy(50, seed=21)).drifted is False


def test_collapsed_live_book_is_caught():
    env = provision_envelope(_healthy(), n_sims=400, seed=1)
    collapsed = _log(np.full(60, -1.5))
    v = check_account_drift(env, collapsed)
    assert v.drifted is True and "cum_r" in v.breaches


# --- currency is presentation only ----------------------------------------------------

def test_in_currency_scales_R_by_the_denominator_and_changes_nothing_else():
    env = provision_envelope(_healthy(), n_sims=300, seed=1)
    v = check_account_drift(env, _log(np.full(60, -1.5)))
    rep = in_currency(v, r_denominator=500.0, currency="USD")
    assert rep["cum"] == pytest.approx(v.cum_r * 500.0)
    assert rep["max_dd_floor"] == pytest.approx(v.max_dd_floor * 500.0)
    assert rep["drifted"] is v.drifted            # the decision is untouched
    assert rep["currency"] == "USD"


def test_currency_reporting_cannot_flip_a_verdict():
    env = provision_envelope(_healthy(), n_sims=300, seed=1)
    v = check_account_drift(env, _healthy(50, seed=21))
    for k in (1.0, 250.0, 10_000.0):
        assert in_currency(v, r_denominator=k)["drifted"] is v.drifted


def test_nonsense_denominator_is_rejected():
    env = provision_envelope(_healthy(), n_sims=200, seed=1)
    v = check_account_drift(env, _healthy(30, seed=9))
    for bad in (0.0, -5.0, float("nan")):
        with pytest.raises(ValueError, match="finite and positive"):
            in_currency(v, r_denominator=bad)
