"""capital.equity — the R-log -> currency equity seam (Seam 3).

The capital sim sizes a crucible TradeLog (per-trade R) into a currency equity path.
crucible stays capital-free, so this is the first place currency appears and the only
place the 1R<->currency denominator lives.
"""
import numpy as np
import pandas as pd
import pytest
from crucible.edge import TradeLog

from crucible_stack.capital import EquityResult, simulate_equity


def _log(r, *, freq="10D", hold_days=6, start="2015-01-05"):
    """R-multiples -> a TradeLog with entry/exit dates (what the sim needs)."""
    entry = pd.date_range(start, periods=len(r), freq=freq)
    exit_ = entry + pd.Timedelta(days=hold_days)
    return TradeLog.from_frame(pd.DataFrame({"r": r, "entry_date": entry, "exit_date": exit_}))


# ── sizing + compounding ───────────────────────────────────────────────────────

def test_fixed_fractional_compounds_r_into_currency():
    r = np.full(4, 1.0)                       # four clean +1R winners
    res = simulate_equity(_log(r), starting_capital=100_000, risk_pct=0.01)
    # 1% of a rising balance: 100000 -> 101000 -> 102010 -> 103030.1 -> 104060.4
    expected = [100_000, 101_000, 102_010, 103_030.1, 104_060.401]
    np.testing.assert_allclose(res.equity.to_numpy(), expected, rtol=1e-9)


def test_equity_is_seeded_with_the_starting_point():
    res = simulate_equity(_log(np.full(5, 0.5)), starting_capital=50_000, risk_pct=0.02)
    assert len(res.equity) == 6              # n trades + the pre-trade seed
    assert res.equity.iloc[0] == 50_000
    assert len(res.returns) == 5
    assert len(res.trades) == 5


def test_costs_reduce_each_trades_pnl():
    r = np.array([1.0])
    free = simulate_equity(_log(r), starting_capital=100_000, risk_pct=0.01)
    costed = simulate_equity(_log(r), starting_capital=100_000, risk_pct=0.01,
                             commission=5.0, slippage_r=0.1)
    # risk$ = 1000: free pnl = 1000; costed = (1.0-0.1)*1000 - 5 = 895
    assert free.trades["pnl"].iloc[0] == pytest.approx(1000.0)
    assert costed.trades["pnl"].iloc[0] == pytest.approx(895.0)


def test_fixed_fractional_never_ruins():
    # 40 straight full stop-outs at 3% risk still can't drive equity <= 0.
    res = simulate_equity(_log(np.full(40, -1.0)), starting_capital=100_000, risk_pct=0.03)
    assert (res.equity > 0).all()
    assert res.equity.iloc[-1] < 100_000
    assert res.stats.max_dd < 0


# ── stats ──────────────────────────────────────────────────────────────────────

def test_max_drawdown_is_worst_peak_to_trough():
    # up to 110k, then down to 99k: trough vs the 110k peak = -10%.
    r = np.array([0.5, 0.5, -1.0, -1.0])     # 1R = 1000 fixed-ish early
    res = simulate_equity(_log(r), starting_capital=100_000, risk_pct=0.01)
    assert res.stats.max_dd < 0
    peak = res.equity.cummax()
    assert res.stats.max_dd == pytest.approx(float((res.equity / peak - 1).min()))


def test_flat_book_scores_zero_sharpe_not_nan():
    res = simulate_equity(_log(np.zeros(10)), starting_capital=100_000, risk_pct=0.01)
    assert res.stats.sharpe == 0.0           # crucible convention: no dispersion -> 0
    assert res.stats.sortino == 0.0          # no edge, no downside -> flat


def test_homogeneous_losses_dont_explode_sortino():
    # Every loss a clean -1R (identical -risk_pct return) -> zero downside dispersion.
    # Naive mean/dd_std blows up to ~1e14 on float noise; guarded -> undefined (NaN).
    r = np.array([2.0, 2.0, -1.0, 2.0, -1.0, 2.0, -1.0, 2.0])   # varied wins, flat losses
    res = simulate_equity(_log(r), starting_capital=100_000, risk_pct=0.01)
    assert np.isnan(res.stats.sortino)
    assert np.isfinite(res.stats.sharpe)     # wins vary -> total dispersion is real


def test_positive_edge_grows_capital():
    rng = np.random.default_rng(0)
    r = rng.normal(0.3, 0.8, 300)            # a real edge, enough draws to realize it
    res = simulate_equity(_log(r), starting_capital=100_000, risk_pct=0.01)
    assert res.equity.iloc[-1] > 100_000
    assert res.stats.cagr > 0
    assert res.stats.sharpe > 0


def test_exposure_needs_entry_dates():
    res = simulate_equity(_log(np.full(5, 0.2)), starting_capital=100_000, risk_pct=0.01)
    assert 0.0 < res.stats.exposure <= 2.0   # time-weighted; >1 only under overlap
    # without entry_date -> NaN (can't measure time in market)
    tl = TradeLog.from_frame(pd.DataFrame({
        "r": [0.2, 0.2], "exit_date": pd.to_datetime(["2015-01-10", "2015-01-20"])}))
    assert np.isnan(simulate_equity(tl, risk_pct=0.01).stats.exposure)


# ── the seam contract ──────────────────────────────────────────────────────────

def test_meta_records_the_full_capital_model():
    res = simulate_equity(_log(np.full(3, 0.5)), starting_capital=100_000,
                          risk_pct=0.01, commission=2.0, slippage_r=0.05, currency="EUR")
    for k in EquityResult.REQUIRED_META:
        assert k in res.meta
    assert res.meta["sizing_model"] == "fixed_fractional"
    assert res.meta["currency"] == "EUR"
    assert res.meta["r_denominator"] == pytest.approx(1000.0)   # 1% of 100k, initial 1R


def test_equity_result_requires_full_meta():
    res = simulate_equity(_log(np.full(3, 0.5)), risk_pct=0.01)
    with pytest.raises(ValueError, match="missing required keys"):
        EquityResult(equity=res.equity, returns=res.returns, trades=res.trades,
                     stats=res.stats, meta={"currency": "USD"})


def test_trades_are_in_currency_not_r():
    res = simulate_equity(_log(np.array([2.0])), starting_capital=100_000, risk_pct=0.01)
    row = res.trades.iloc[0]
    assert row["r"] == 2.0                    # R preserved for reference
    assert row["pnl"] == pytest.approx(2000.0)  # ...but pnl/risk_amount are currency
    assert row["risk_amount"] == pytest.approx(1000.0)


# ── per-trade sizes (the corr_scaled / count_cap wiring) ──────────────────────

def test_constant_sizes_equal_scalar_risk_pct():
    r = np.array([1.0, -0.5, 2.0, -1.0, 0.5])
    a = simulate_equity(_log(r), starting_capital=100_000, risk_pct=0.02)
    b = simulate_equity(_log(r), starting_capital=100_000, sizes=np.full(len(r), 0.02))
    np.testing.assert_allclose(a.equity.to_numpy(), b.equity.to_numpy(), rtol=1e-12)
    assert a.meta["sizing_model"] == "fixed_fractional"
    assert b.meta["sizing_model"] == "per_trade"


def test_sizes_size_each_trade_and_are_recorded():
    sizes = np.array([0.01, 0.02])                 # second trade risks twice as much
    res = simulate_equity(_log(np.array([2.0, 2.0])), starting_capital=100_000, sizes=sizes)
    np.testing.assert_allclose(res.trades["risk_frac"].to_numpy(), sizes)
    assert res.trades["pnl"].iloc[0] == pytest.approx(2000.0)    # 2R · 1% · 100k
    assert res.trades["pnl"].iloc[1] == pytest.approx(4080.0)    # 2R · 2% · 102k


def test_sizes_override_risk_pct():
    res = simulate_equity(_log(np.full(4, 1.0)), risk_pct=0.99, sizes=np.full(4, 0.01))
    assert (res.trades["risk_frac"] == 0.01).all()              # risk_pct ignored


def test_scalar_case_records_risk_frac():
    res = simulate_equity(_log(np.full(3, 0.5)), risk_pct=0.01)
    assert (res.trades["risk_frac"] == 0.01).all()


def test_sizes_follow_trades_through_the_exit_sort():
    # rows are in reverse-exit order; sizes[i] belongs to row i. After the internal
    # exit-date sort each fraction must still be paired with its own trade.
    tl = TradeLog.from_frame(pd.DataFrame(
        {"r": [1.0, 1.0, 1.0],
         "exit_date": pd.to_datetime(["2015-03-01", "2015-02-01", "2015-01-01"])}))
    res = simulate_equity(tl, sizes=np.array([0.03, 0.02, 0.01]))
    np.testing.assert_allclose(res.trades["risk_frac"].to_numpy(), [0.01, 0.02, 0.03])


def test_sizes_length_must_match():
    with pytest.raises(ValueError, match="one risk fraction per trade"):
        simulate_equity(_log(np.full(4, 0.5)), sizes=np.array([0.01, 0.01]))


def test_sizes_must_be_valid_fractions():
    good = _log(np.full(3, 0.5))
    with pytest.raises(ValueError, match="sizes must be"):
        simulate_equity(good, sizes=np.array([0.01, 1.5, 0.01]))
    with pytest.raises(ValueError, match="all zero"):
        simulate_equity(good, sizes=np.zeros(3))


# ── guards ─────────────────────────────────────────────────────────────────────

def test_requires_exit_date():
    with pytest.raises(ValueError, match="exit_date"):
        simulate_equity(TradeLog.from_arrays(r=[1.0, -1.0]), risk_pct=0.01)


def test_rejects_empty_log():
    tl = TradeLog.from_frame(pd.DataFrame({"r": [], "exit_date": pd.to_datetime([])}))
    with pytest.raises(ValueError, match="empty"):
        simulate_equity(tl, risk_pct=0.01)


@pytest.mark.parametrize("bad", [0.0, 1.0, 1.5, -0.1])
def test_rejects_out_of_range_risk(bad):
    with pytest.raises(ValueError, match="risk_pct"):
        simulate_equity(_log(np.full(3, 0.5)), risk_pct=bad)


# ── composes with barrier_trades end-to-end ────────────────────────────────────

def test_consumes_a_real_barrier_trades_log():
    from crucible.edge import barrier_trades
    rng = np.random.default_rng(3)
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, 400))
    idx = pd.date_range("2015-01-01", periods=400, freq="D")
    ohlc = pd.DataFrame({"Open": close, "High": close * 1.005,
                         "Low": close * 0.995, "Close": close}, index=idx)
    entries = pd.Series(False, index=idx)
    entries.iloc[::20] = True
    tl = barrier_trades(ohlc, entries, side="long", tp=2.0, sl=1.0,
                        timeout=15, risk_unit="price")
    assert tl.n > 0
    res = simulate_equity(tl, starting_capital=25_000, risk_pct=0.015)
    assert isinstance(res, EquityResult)
    assert len(res.trades) == tl.n
    assert np.isfinite(res.stats.cagr)
