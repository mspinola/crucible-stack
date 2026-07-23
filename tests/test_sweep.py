"""sweep — the plain-loop optimizer driver (strategy + grid -> TrialMatrix)."""
import numpy as np
import pandas as pd
import pytest
from crucible.edge import barrier_trades
from crucible.strategies import ma_cross
from crucible.validation import pbo_cscv
from crucible.validation.pbo import deflated_sharpe

from crucible_stack.optimize import TrialMatrix, sweep


def _ohlc(n=520, seed=1):
    """Synthetic daily OHLC (a gentle random walk) — enough bars for ~24 months."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2015-01-01", periods=n, freq="B")
    close = 100 + np.cumsum(rng.normal(0.05, 1.0, n))
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    open_ = close - rng.normal(0, 0.5, n)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close}, index=idx)


def test_sweep_returns_a_trial_matrix_in_R():
    px = _ohlc()
    tm = sweep(px, ma_cross, {"fast": [5, 10], "slow": [20, 30]}, timeout=15)
    assert isinstance(tm, TrialMatrix)
    assert tm.n_trials <= 4
    assert isinstance(tm.returns.index, pd.DatetimeIndex)
    assert tm.meta["engine"] == "barrier_trades"        # one faithful sim, native R
    assert tm.fixed["tp"] == 2.0 and tm.fixed["sl"] == 1.0 and tm.fixed["side"] == "long"


def test_honest_n_counts_every_config_tried_even_if_no_column():
    px = _ohlc()
    tm = sweep(px, ma_cross, {"fast": [5, 10], "slow": [20, 30]}, timeout=15)
    assert tm.honest_n == 4
    assert tm.n_trials <= tm.honest_n


def test_composes_with_crucible_corrections():
    px = _ohlc()
    tm = sweep(px, ma_cross, {"fast": [5, 10], "slow": [20, 30]}, timeout=15)
    if tm.n_trials >= 2 and tm.returns.shape[0] >= 4:
        assert 0.0 <= pbo_cscv(tm.returns, S=4).pbo <= 1.0
        sharpes = tm.trial_sharpes()
        ds = deflated_sharpe(sharpes.to_numpy(), returns=tm.returns_for(sharpes.idxmax()))
        assert 0.0 <= ds.deflated_sharpe <= 1.0


def test_small_search_is_enforced():
    px = _ohlc()
    big = {"fast": list(range(3, 12)), "slow": list(range(20, 30))}   # 90 configs
    with pytest.raises(ValueError, match="MAX_CONFIGS"):
        sweep(px, ma_cross, big)


def test_errored_config_is_counted_not_fatal():
    px = _ohlc()

    def flaky(prices, fast, slow):
        if fast == 999:
            raise RuntimeError("boom")
        return ma_cross(prices, fast=fast, slow=slow)

    tm = sweep(px, flaky, {"fast": [5, 999], "slow": [20]}, timeout=15)
    assert tm.honest_n == 2          # both tried (one errored) -> on the ledger
    assert tm.n_trials <= 1          # the errored one is not a column
    assert "fast=999" not in tm.configs


def test_default_engine_is_barrier_trades():
    tm = _swept()
    assert tm.meta["engine"] == "barrier_trades"
    assert tm.fixed["tp"] == 2.0 and tm.fixed["sl"] == 1.0    # barrier params recorded


def _swept(seed=1):
    return sweep(_ohlc(seed=seed), ma_cross, {"fast": [5, 10], "slow": [20, 30]}, timeout=15)


def test_custom_simulator_hook_is_used_per_config():
    # A pluggable simulator receives (prices, entries, side, config) and replaces
    # the default. Barrier params must not be recorded as fixed for a custom sim.
    px = _ohlc()
    seen = []

    def custom_sim(prices, entries, side, config):
        seen.append(config)
        return barrier_trades(prices, entries, side=side, tp=3.0, sl=1.0, timeout=10)
    custom_sim.name = "custom_x"

    tm = sweep(px, ma_cross, {"fast": [5, 10], "slow": [20, 30]}, simulate=custom_sim)
    assert tm.meta["engine"] == "custom_x"
    assert len(seen) == 4                                     # called once per config
    assert "tp" not in tm.fixed and "timeout" not in tm.fixed  # no default-sim params leaked


def test_rules_simulator_requires_specs():
    import pytest

    from crucible_stack.optimize import rules_simulator
    with pytest.raises(ValueError, match="spec_fn or both"):
        rules_simulator(is_equity=False)                     # neither specs nor spec_fn
