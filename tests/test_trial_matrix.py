"""TrialMatrix — the optimizer -> crucible seam."""
import numpy as np
import pandas as pd
import pytest
from crucible.edge import TradeLog
from crucible.validation import SearchSpaceLog, pbo_cscv
from crucible.validation.pbo import deflated_sharpe

from crucible_stack.optimize import TrialMatrix


def _months(n):
    return pd.date_range("2015-01-31", periods=n, freq="ME")


def _sample_trials(n_periods=24, seed=0):
    """config_id -> (params, periodic-R series). Four MA configs on a shared calendar."""
    rng = np.random.default_rng(seed)
    idx = _months(n_periods)
    trials = {}
    for i, (fast, slow) in enumerate([(10, 50), (20, 50), (10, 100), (20, 100)]):
        trials[f"f{fast}_s{slow}"] = ({"fast": fast, "slow": slow},
                                      pd.Series(rng.normal(0.05, 1.0, n_periods), index=idx))
    return trials


def _meta():
    return {"engine": "test", "search_space": {"fast": [10, 20], "slow": [50, 100]}}


def _built(**over):
    kw = dict(objective="deflated_sharpe", fixed={"kind": "sma", "tp": 2.0}, meta=_meta())
    kw.update(over)
    return TrialMatrix.from_trials(_sample_trials(), **kw)


# ── construction ─────────────────────────────────────────────────────────────

def test_from_trials_assembles_periods_by_configs():
    tm = _built()
    assert tm.returns.shape == (24, 4)
    assert set(tm.returns.columns) == set(tm.configs)
    assert isinstance(tm.returns.index, pd.DatetimeIndex)


def test_from_trials_outer_joins_and_fills_missing_with_zero():
    # Two configs that trade in disjoint months -> union index, 0 where absent.
    idx_a, idx_b = _months(3), _months(6)[3:]
    trials = {
        "a": ({"p": 1}, pd.Series([1.0, -1.0, 0.5], index=idx_a)),
        "b": ({"p": 2}, pd.Series([0.2, 0.3, -0.4], index=idx_b)),
    }
    tm = TrialMatrix.from_trials(trials, objective="expectancy", fixed={}, meta=_meta())
    assert tm.returns.shape == (6, 2)
    assert tm.returns.loc[idx_a[0], "b"] == 0.0     # b didn't trade month 1 -> flat
    assert tm.returns.loc[idx_b[-1], "a"] == 0.0


# ── validation ───────────────────────────────────────────────────────────────

def test_columns_must_match_configs():
    tm = _built()
    with pytest.raises(ValueError, match="match 1:1"):
        TrialMatrix(returns=tm.returns, configs={"only_one": {}},
                    objective="x", fixed={}, meta=_meta())


def test_returns_must_be_datetime_indexed():
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0]})  # RangeIndex
    with pytest.raises(ValueError, match="DatetimeIndex"):
        TrialMatrix(returns=df, configs={"a": {}}, objective="x", fixed={}, meta=_meta())


def test_meta_requires_provenance():
    tm = _built()
    with pytest.raises(ValueError, match="provenance"):
        TrialMatrix(returns=tm.returns, configs=tm.configs, objective="x",
                    fixed={}, meta={"engine": "test"})  # missing search_space


def test_trade_logs_keys_must_be_known_configs():
    tm = _built()
    stray = {"ghost": TradeLog.from_arrays(r=[1.0, -1.0])}
    with pytest.raises(ValueError, match="absent from configs"):
        TrialMatrix(returns=tm.returns, configs=tm.configs, objective="x",
                    fixed={}, meta=_meta(), trade_logs=stray)


def test_empty_is_rejected():
    with pytest.raises(ValueError):
        TrialMatrix.from_trials({}, objective="x", fixed={}, meta=_meta())


# ── the n_trials vs honest_n distinction (the whole point) ────────────────────

def test_n_trials_is_column_count():
    assert _built().n_trials == 4


def test_honest_n_refuses_to_guess_without_ledger():
    tm = _built()  # no search_log
    with pytest.raises(ValueError, match="honest_n is unknowable"):
        _ = tm.honest_n


def test_honest_n_exceeds_n_trials_when_variants_failed_to_score():
    # The ledger recorded 6 variants tried; only 4 produced return columns
    # (2 errored / scored too few trades). honest_n must be 6, not 4.
    log = SearchSpaceLog(scope="test:grid")
    for fast in (10, 20):
        for slow in (50, 100, 200):        # 6 tried
            log.record({"fast": fast, "slow": slow}, status="tried")
    tm = _built(search_log=log)
    assert tm.n_trials == 4
    assert tm.honest_n == 6
    assert tm.honest_n >= tm.n_trials       # the invariant


# ── crucible handoff ─────────────────────────────────────────────────────────

def test_trial_sharpes_matches_crucible_convention_zero_for_flat():
    idx = _months(12)
    trials = {
        "live": ({"p": 1}, pd.Series(np.linspace(-1, 1, 12), index=idx)),
        "flat": ({"p": 2}, pd.Series(np.zeros(12), index=idx)),   # zero dispersion
    }
    tm = TrialMatrix.from_trials(trials, objective="x", fixed={}, meta=_meta())
    s = tm.trial_sharpes()
    assert s["flat"] == 0.0                 # crucible scores no-dispersion as 0, not NaN
    assert np.isfinite(s["live"])


def test_composes_with_pbo_cscv_and_deflated_sharpe():
    # The seam earns its keep only if crucible consumes it directly.
    tm = _built()
    pbo = pbo_cscv(tm.returns, S=4)         # .returns IS the CSCV matrix
    assert 0.0 <= pbo.pbo <= 1.0

    sharpes = tm.trial_sharpes()
    winner = sharpes.idxmax()
    ds = deflated_sharpe(sharpes.to_numpy(), returns=tm.returns_for(winner))
    assert 0.0 <= ds.deflated_sharpe <= 1.0


def test_log_for_returns_kept_winner_log():
    tm0 = _built()
    winner = tm0.trial_sharpes().idxmax()
    log = {winner: TradeLog.from_arrays(r=[1.0, -0.5, 2.0, -1.0])}
    tm = TrialMatrix.from_trials(_sample_trials(), objective="deflated_sharpe",
                                 fixed={}, meta=_meta(), trade_logs=log)
    assert tm.log_for(winner) is not None
    assert tm.log_for("f20_s100") is None or winner == "f20_s100"
