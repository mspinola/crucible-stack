"""The edge-decay seam: freeze a baseline at promotion, read it back, trigger on it.

The judging is crucible's. What is tested here is the seam: that the baseline survives
the ledger round trip, that nothing can rebuild it, and that only the calibrated channel
is allowed to force a re-optimization.
"""
import inspect
from datetime import datetime

import numpy as np
import pytest

from crucible_stack.orchestrate import (
    DeploymentEntry,
    DeploymentLedger,
    EdgeDecayTrigger,
    TriggerContext,
    baseline_from_dict,
    baseline_to_dict,
    check_decay,
)
from crucible_stack.orchestrate.decay import EdgeBaseline, Thresholds


def _baseline(**kw):
    return EdgeBaseline(**{"expectancy": 0.2, "sigma": 1.0, "n_trades": 2000,
                           "trades_per_year": 150.0, "n_variants": 64,
                           "deflated": True, **kw})


def _r(mu, n=1500, seed=0):
    return np.random.default_rng(seed).normal(mu, 1.0, n)


# ── serialization: the baseline has to survive the ledger ───────────────────────────

def test_baseline_round_trips_exactly():
    b = _baseline()
    assert baseline_from_dict(baseline_to_dict(b)) == b


def test_round_trip_survives_the_optional_fields_being_absent():
    b = EdgeBaseline(expectancy=0.2, sigma=1.0, n_trades=500)
    back = baseline_from_dict(baseline_to_dict(b))
    assert back == b
    assert back.trades_per_year is None and back.n_variants is None
    assert back.deflated is False        # and it still says so


def test_baseline_from_dict_passes_through_none():
    assert baseline_from_dict(None) is None
    assert baseline_from_dict({}) is None


def test_a_corrupted_entry_raises_rather_than_yielding_a_monitor_that_cannot_fire():
    """Reconstruction re-runs crucible's validation. A hand-edited non-positive
    expectancy is caught here, not discovered as an alarm that never arrives."""
    bad = baseline_to_dict(_baseline()) | {"expectancy": -0.1}
    with pytest.raises(ValueError, match="must be positive"):
        baseline_from_dict(bad)


def test_ledger_entry_round_trips_the_baseline_through_json(tmp_path):
    entry = DeploymentEntry(
        book="b", timestamp=datetime(2026, 1, 2), action="promote", trigger="schedule",
        params={"fast": 10}, verdict="PASS", trustworthy=True, baseline=_baseline())
    back = DeploymentEntry.from_json(entry.to_json())
    assert back.baseline == entry.baseline

    led = DeploymentLedger(str(tmp_path / "d.jsonl"))
    led.record(entry)
    assert DeploymentLedger(str(tmp_path / "d.jsonl")).current("b").baseline == _baseline()


def test_an_entry_without_a_baseline_still_round_trips():
    entry = DeploymentEntry(book="b", timestamp=datetime(2026, 1, 2), action="hold",
                            trigger="schedule", params={}, verdict="FAIL",
                            trustworthy=False)
    assert DeploymentEntry.from_json(entry.to_json()).baseline is None


# ── the trigger ─────────────────────────────────────────────────────────────────────

def test_a_decayed_edge_fires():
    ctx = TriggerContext(trade_r=_r(0.05, n=3000), baseline=_baseline())
    d = EdgeDecayTrigger(thresholds=Thresholds(monitor_arl0_trades=500))(ctx)
    assert d.fired and d.sources == ("edge_decay",)


def test_a_healthy_edge_does_not_fire():
    ctx = TriggerContext(trade_r=_r(0.3, n=1500, seed=2), baseline=_baseline())
    assert not EdgeDecayTrigger()(ctx).fired


def test_slipping_is_reported_but_does_not_trigger():
    """The whole reason crucible separates the channels. A firing-rate collapse or a
    trailing-window dip is uncalibrated, so it must not force a re-optimization."""
    baseline = _baseline(trades_per_year=150.0)
    # per-trade edge intact; nothing here can reach DEGRADED
    ctx = TriggerContext(trade_r=_r(0.2, n=300, seed=4), baseline=baseline)
    v = check_decay(baseline, ctx.trade_r)
    d = EdgeDecayTrigger()(ctx)
    if v.label == "SLIPPING":
        assert not d.fired
        assert any("does NOT trigger" in r for r in d.reasons)
    else:
        assert v.label == "HOLDING" and not d.fired


def test_a_missing_baseline_fails_open_like_drift():
    """Unmonitored is not the same as healthy. Matches DriftTrigger's posture."""
    d = EdgeDecayTrigger()(TriggerContext(trade_r=_r(0.2, n=100), baseline=None))
    assert d.fired
    assert any("no baseline attached" in r for r in d.reasons)


def test_no_incumbent_is_a_cold_start():
    d = EdgeDecayTrigger()(TriggerContext(has_incumbent=False))
    assert d.fired and any("no incumbent" in r for r in d.reasons)


def test_no_closed_trades_yet_is_quiet():
    d = EdgeDecayTrigger()(TriggerContext(trade_r=np.zeros(0), baseline=_baseline()))
    assert not d.fired
    assert any("no closed trades yet" in r for r in d.reasons)


# ── the seam's own invariants ───────────────────────────────────────────────────────

def test_per_trade_and_periodic_r_are_separate_clocks():
    """`realized_r` is periodic (the envelope's grid); `trade_r` is per-trade. Two
    different series with two different lengths. Denominating one in the other is the
    units bug crucible fixed in v0.4.0."""
    ctx = TriggerContext(realized_r=np.zeros(12), trade_r=np.zeros(400))
    assert ctx.elapsed == 12 and ctx.trades_live == 400


def test_trade_r_must_be_one_dimensional():
    with pytest.raises(ValueError, match="trade_r must be 1-D"):
        TriggerContext(trade_r=np.zeros((3, 4)))


# ── the firing-rate channel ─────────────────────────────────────────────────────────

def _dates(n, *, per_year, start="2024-01-01"):
    """n entry dates spaced to fire at `per_year`."""
    step = 365.25 / per_year
    return np.array([np.datetime64(start) + np.timedelta64(int(round(i * step)), "D")
                     for i in range(n)])


def test_without_dates_the_firing_rate_channel_is_off():
    """The regression this block exists for. `check_decay` built its log from bare floats,
    so crucible could never derive a live rate: the channel was dead for every book through
    the orchestrator, however carefully the baseline's own rate had been frozen."""
    v = check_decay(_baseline(), _r(0.2, n=300, seed=4))
    assert v.live_trades_per_year is None and v.frequency_ratio is None
    assert any("no entry_date" in r for r in v.reasons)


def test_with_dates_the_channel_reports_a_ratio():
    n = 300
    v = check_decay(_baseline(trades_per_year=150.0), _r(0.2, n=n, seed=4),
                    trade_dates=_dates(n, per_year=150.0))
    assert v.live_trades_per_year == pytest.approx(150.0, rel=0.02)
    assert v.frequency_ratio == pytest.approx(1.0, rel=0.02)


def test_a_signal_that_stopped_firing_is_caught_only_by_the_dated_channel():
    """The failure mode the channel exists for: per-trade expectancy is INTACT, so the
    CUSUM and the rolling window both read healthy and only the rate has collapsed."""
    n = 300
    r = _r(0.2, n=n, seed=4)                        # edge exactly at baseline
    baseline = _baseline(trades_per_year=150.0)
    assert check_decay(baseline, r).label == "HOLDING"           # undated: blind

    v = check_decay(baseline, r, trade_dates=_dates(n, per_year=30.0))
    assert v.frequency_ratio == pytest.approx(0.2, rel=0.02)
    assert v.label == "SLIPPING"
    assert any("fires at" in x for x in v.reasons)


def test_a_collapsed_firing_rate_reports_but_does_not_trigger():
    """Uncalibrated, so it caps at SLIPPING. Same rule as the rolling window."""
    n = 300
    ctx = TriggerContext(trade_r=_r(0.2, n=n, seed=4),
                         baseline=_baseline(trades_per_year=150.0),
                         trade_dates=_dates(n, per_year=30.0))
    d = EdgeDecayTrigger()(ctx)
    assert not d.fired
    assert any("does NOT trigger" in x for x in d.reasons)


def test_misaligned_dates_are_refused_rather_than_mis_dating_the_rate():
    with pytest.raises(ValueError, match="must be parallel"):
        TriggerContext(trade_r=np.zeros(10), trade_dates=_dates(4, per_year=150.0))


def test_trade_dates_must_be_one_dimensional():
    with pytest.raises(ValueError, match="trade_dates must be 1-D"):
        TriggerContext(trade_r=np.zeros(4), trade_dates=np.zeros((2, 2)))


def test_the_trigger_forwards_the_dates_it_was_given():
    """Guards the wire itself. A trigger that dropped `trade_dates` would leave every
    verdict looking exactly like the undated case above, with nothing else to notice."""
    n = 300
    ctx = TriggerContext(trade_r=_r(0.2, n=n, seed=4),
                         baseline=_baseline(trades_per_year=150.0),
                         trade_dates=_dates(n, per_year=30.0))
    undated = TriggerContext(trade_r=ctx.trade_r, baseline=ctx.baseline)
    assert "ctx.trade_dates" in inspect.getsource(EdgeDecayTrigger)
    assert EdgeDecayTrigger()(ctx).reasons != EdgeDecayTrigger()(undated).reasons


def test_check_decay_cannot_rebuild_a_baseline():
    """Same guard crucible puts on edge_monitor, re-asserted at the seam that persists it.

    `trade_dates` describes the LIVE trades, so it cannot reconstruct a reference: it has
    no expectancy in it, only when the trades happened.
    """
    assert set(inspect.signature(check_decay).parameters) == {
        "baseline", "trade_r", "trade_dates", "thresholds"}


def test_the_trigger_reads_the_baseline_and_never_builds_one():
    src = inspect.getsource(EdgeDecayTrigger)
    assert "ctx.baseline" in src
    assert "from_log" not in src and "EdgeBaseline(" not in src
