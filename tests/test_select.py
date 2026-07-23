"""select — pick the honest winner from a TrialMatrix and gate it."""
import numpy as np
import pandas as pd
from crucible.strategies import ma_cross
from crucible.validation import Thresholds

from crucible_stack.optimize import Selection, select, sweep


def _ohlc(n=520, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2015-01-01", periods=n, freq="B")
    close = 100 + np.cumsum(rng.normal(0.05, 1.0, n))
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    open_ = close - rng.normal(0, 0.5, n)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close}, index=idx)


def _swept(seed=1):
    return sweep(_ohlc(seed=seed), ma_cross, {"fast": [5, 10], "slow": [20, 30]}, timeout=15)


def test_selects_highest_sharpe_config():
    tm = _swept()
    s = select(tm, pbo_blocks=4)
    assert isinstance(s, Selection)
    assert s.config_id == tm.trial_sharpes().idxmax()   # coherent with the deflation metric
    assert s.params == tm.configs[s.config_id]
    assert s.honest_n == tm.honest_n


def test_all_three_corrections_run():
    s = select(_swept(), pbo_blocks=4)
    assert 0.0 <= s.deflated_sharpe <= 1.0
    assert np.isnan(s.pbo) or 0.0 <= s.pbo <= 1.0
    assert s.reality is not None and s.reality.label in ("HELD", "FRAGILE", "FAIL")


def test_random_walk_is_not_trustworthy_with_reasons():
    # No real edge -> the honest gate must reject it, and say why.
    s = select(_swept(), pbo_blocks=4)
    assert s.trustworthy is False
    assert any("deflated_sharpe" in r for r in s.reasons)


def test_marking_updates_ledger_in_place_no_inflation():
    tm = _swept()
    before = tm.honest_n
    s = select(tm, pbo_blocks=4)
    assert tm.honest_n == before                        # mark_selected did not add a variant
    selected = [e for e in tm.search_log.entries if e["status"] == "selected"]
    assert len(selected) == 1 and selected[0]["params"] == s.params


def test_mark_false_leaves_ledger_untouched():
    tm = _swept()
    select(tm, pbo_blocks=4, mark=False)
    assert not any(e["status"] == "selected" for e in tm.search_log.entries)


def test_gate_bars_come_from_thresholds():
    # The overfit bars are read from crucible's central Thresholds, not hardcoded.
    tm = _swept()
    # Default bar (0.95) rejects a random walk's deflated Sharpe...
    strict = select(tm, pbo_blocks=4, mark=False)
    assert any("deflated_sharpe below bar" in r for r in strict.reasons)
    # ...a slackened bar accepts it, proving select honors the passed Thresholds.
    lax = select(tm, pbo_blocks=4, mark=False,
                 thresholds=Thresholds(min_deflated_sharpe=0.0))
    assert any("deflated_sharpe ok" in r for r in lax.reasons)
