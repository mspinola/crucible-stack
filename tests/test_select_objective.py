"""select(objective=...), choosing on something other than Sharpe, visibly.

The situation this exists for: a filter that improves per-trade edge usually cuts trade
count, and a shorter, lumpier monthly series scores worse on Sharpe even as expectancy
rises. Selecting on Sharpe then promotes the unfiltered variant; selecting on expectancy
promotes the filtered one. Both can be defensible.

So the objective has to be explicit rather than assumed, and the correction has to follow
whichever one was chosen. The gate must also say so when the metric it selected on and the
metric it corrected against stop being like-for-like.
"""
import numpy as np
import pandas as pd
import pytest
from crucible.edge import TradeLog
from crucible.strategies import ma_cross
from crucible.validation import SearchSpaceLog

from crucible_stack.optimize import TrialMatrix, select, sweep


def _ohlc(n=420, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2015-01-01", periods=n, freq="B")
    close = 100 + np.cumsum(rng.normal(0.05, 1.0, n))
    return pd.DataFrame({"Open": close - rng.normal(0, 0.5, n),
                         "High": close + rng.uniform(0.1, 1.0, n),
                         "Low": close - rng.uniform(0.1, 1.0, n),
                         "Close": close}, index=idx)


def _swept():
    return sweep(_ohlc(), ma_cross, {"fast": [5, 10], "slow": [20, 30]}, timeout=15)


def _handmade():
    """Two configs where Sharpe and per-trade expectancy disagree by construction.

    `often` trades a lot at a small edge (steady, high Sharpe); `rare` trades seldom at a
    big edge (lumpy, lower Sharpe) — the trend book's shape in miniature.
    """
    idx = pd.date_range("2015-01-31", periods=48, freq="ME")
    # steady: 6 trades/mo at 0.10R -> 0.60R/mo, alternating 0.9/0.3 so it has variance
    # (a CONSTANT series has sd 0 and crucible scores it Sharpe 0.0, not infinity)
    often = pd.Series(([0.90, 0.30] * 24)[:48], index=idx)
    # lumpy: 1 trade every 3 months at 1.50R -> 0.50R/mo, far more volatile
    rare = pd.Series(([0.0, 0.0, 1.50] * 16)[:48], index=idx)
    log = SearchSpaceLog(scope="t")
    for p in ({"k": "often"}, {"k": "rare"}):
        log.record(p, status="tried")
    logs = {"often": TradeLog.from_arrays(r=np.full(6 * 48, 0.10)),
            "rare": TradeLog.from_arrays(r=np.full(16, 1.50))}
    return TrialMatrix.from_trials(
        {"often": ({"k": "often"}, often), "rare": ({"k": "rare"}, rare)},
        objective="x", fixed={}, meta={"engine": "t", "search_space": {}},
        trade_logs=logs, search_log=log)


# --- the default is unchanged ---------------------------------------------------------

def test_sharpe_is_the_default_and_stays_coherent():
    s = select(_swept(), pbo_blocks=4)
    assert s.objective == "sharpe" and s.coherent is True
    assert not any("NOT the same metric" in r for r in s.reasons)


def test_explicit_sharpe_matches_the_default():
    a, b = select(_swept(), pbo_blocks=4, mark=False), \
           select(_swept(), objective="sharpe", pbo_blocks=4, mark=False)
    assert a.config_id == b.config_id and a.deflated_sharpe == b.deflated_sharpe


# --- selecting on something else ------------------------------------------------------

def test_expectancy_and_sharpe_can_pick_different_configs():
    tm = _handmade()
    by_sharpe = select(tm, pbo_blocks=4, mark=False)
    by_exp = select(tm, objective="expectancy", pbo_blocks=4, mark=False)
    assert by_sharpe.config_id == "often"      # steady beats lumpy on Sharpe
    assert by_exp.config_id == "rare"          # ...and loses badly on per-trade R
    assert by_exp.objective_score == pytest.approx(1.50)


def test_mean_r_rewards_trading_often_where_expectancy_does_not():
    """Per-PERIOD R cannot tell 'traded twice as well' from 'traded twice as often'."""
    tm = _handmade()
    assert select(tm, objective="mean_r", pbo_blocks=4, mark=False).config_id == "often"
    assert select(tm, objective="expectancy", pbo_blocks=4, mark=False).config_id == "rare"


# --- the correction follows the objective -------------------------------------

def test_sharpe_is_corrected_by_deflated_sharpe():
    s = select(_swept(), pbo_blocks=4, mark=False)
    assert s.correction == "deflated_sharpe" and s.coherent is True
    assert np.isnan(s.spa_pvalue)
    assert any(r.startswith("deflated_sharpe") for r in s.reasons)


@pytest.mark.parametrize("objective", ["expectancy", "mean_r"])
def test_mean_based_objectives_are_corrected_by_spa(objective):
    """Deflated Sharpe prices a search over SHARPE. Selecting on a mean and correcting
    with it would test a statistic nobody selected on — so these route to SPA."""
    s = select(_handmade(), objective=objective, pbo_blocks=4, mark=False)
    assert s.correction == "spa" and s.coherent is True
    assert 0.0 <= s.spa_pvalue <= 1.0 and np.isnan(s.deflated_sharpe)
    assert any("corrected on" in r and objective in r for r in s.reasons)


def test_the_spa_gate_decides_trustworthiness_for_a_mean_objective():
    s = select(_handmade(), objective="expectancy", pbo_blocks=4, mark=False)
    spa_reason = [r for r in s.reasons if r.startswith("spa")][0]
    assert ("ok" in spa_reason) == (s.spa_pvalue < 0.05)


def test_expectancy_corrects_on_per_trade_R_not_per_period():
    """The statistic selected on is per-TRADE, so that is the series SPA must see."""
    from crucible_stack.optimize.select import OBJECTIVES, _per_trade_series
    assert OBJECTIVES["expectancy"]["spa"] is _per_trade_series
    tm = _handmade()
    series = _per_trade_series(tm)
    assert len(series["rare"]) == 16 and len(series["often"]) == 6 * 48


def test_pbo_ranks_on_the_selected_metric():
    """PBO asks 'did IS winning carry OOS'. It must ask it about the same statistic."""
    from crucible_stack.optimize.select import OBJECTIVES, _mean_cols
    assert OBJECTIVES["sharpe"]["pbo"] is None           # crucible's default is Sharpe
    assert OBJECTIVES["mean_r"]["pbo"] is _mean_cols
    assert OBJECTIVES["expectancy"]["pbo"] is _mean_cols


# --- a custom scorer is the one case that stays incoherent, loudly --------------------

def test_a_custom_objective_cannot_be_matched_and_says_so():
    """We cannot know what a callable optimizes, so the corrections stay on Sharpe."""
    def prefer_rare(tm):
        return pd.Series({c: (1.0 if c == "rare" else 0.0) for c in tm.returns.columns})
    s = select(_handmade(), objective=prefer_rare, pbo_blocks=4, mark=False)
    assert s.coherent is False and s.correction == "deflated_sharpe"
    assert any("NOT the same metric" in r for r in s.reasons)
    assert any("custom scorer" in r for r in s.reasons)


def test_expectancy_without_trade_logs_cannot_run_spa():
    tm = sweep(_ohlc(), ma_cross, {"fast": [5, 10]}, timeout=15, keep_logs=False)
    with pytest.raises(ValueError, match="keep_logs"):
        select(tm, objective="expectancy", pbo_blocks=4, mark=False)


# --- plumbing -------------------------------------------------------------------------

def test_a_callable_objective_is_accepted():
    def prefer_rare(tm):
        return pd.Series({c: (1.0 if c == "rare" else 0.0) for c in tm.returns.columns})
    s = select(_handmade(), objective=prefer_rare, pbo_blocks=4, mark=False)
    assert s.config_id == "rare" and s.objective == "prefer_rare"


def test_an_unknown_objective_names_the_alternatives():
    with pytest.raises(ValueError, match="unknown objective"):
        select(_swept(), objective="sortino", pbo_blocks=4, mark=False)



