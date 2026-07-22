"""sweep(search_log=...), pricing a search that spans several books as ONE search.

The gap this closes: scan many markets with a few configurations each, and if every sweep
opens its own ledger then each market is corrected only for its own handful of variants,
blind to all the others. Two markets can then come back at 98% and 99% deflated Sharpe and
collapse to near zero once priced against the whole scan. At a 5% level, a 45-market scan
expects around 2.2 false passes by chance alone, so two apparent survivors is what noise
looks like rather than evidence.

Sharing one `SearchSpaceLog` across the sweeps is what makes the denominator honest.
"""
import numpy as np
import pandas as pd
import pytest

from crucible.strategies import ma_cross
from crucible.validation import SearchSpaceLog

from crucible_stack.optimize import select, sweep


def _ohlc(n=400, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2015-01-01", periods=n, freq="B")
    close = 100 + np.cumsum(rng.normal(0.05, 1.0, n))
    return pd.DataFrame({"Open": close - rng.normal(0, 0.5, n),
                         "High": close + rng.uniform(0.1, 1.0, n),
                         "Low": close - rng.uniform(0.1, 1.0, n),
                         "Close": close}, index=idx)


def _sweep(seed, log=None, scope="mkt"):
    return sweep(_ohlc(seed=seed), ma_cross, {"fast": [5, 10], "slow": [20, 30]},
                 timeout=15, scope=scope, search_log=log)


# --- the default is unchanged ---------------------------------------------------------

def test_without_a_shared_ledger_each_sweep_counts_only_itself():
    a, b = _sweep(1), _sweep(2)
    assert a.honest_n == 4 and b.honest_n == 4
    assert a.search_log is not b.search_log


# --- the fix --------------------------------------------------------------------------

def test_a_shared_ledger_accumulates_across_sweeps():
    log = SearchSpaceLog(scope="universe-scan")
    a = _sweep(1, log, scope="mkt_a")
    assert a.honest_n == 4
    b = _sweep(2, log, scope="mkt_b")
    assert b.honest_n == 8, "the second sweep must be priced for both searches"
    assert a.search_log is b.search_log is log


def test_the_shared_denominator_is_what_reaches_the_correction():
    log = SearchSpaceLog(scope="universe-scan")
    for i in range(1, 6):
        tm = _sweep(i, log, scope=f"mkt_{i}")
    assert tm.honest_n == 20                       # 5 sweeps x 4 configs
    assert select(tm, pbo_blocks=4).honest_n == 20


def test_entries_stay_attributable_to_their_sweep():
    log = SearchSpaceLog(scope="universe-scan")
    _sweep(1, log, scope="mkt_a")
    _sweep(2, log, scope="mkt_b")
    scopes = {e.get("sweep_scope") for e in log.entries}
    assert scopes == {"mkt_a", "mkt_b"}, "a shared ledger must not lose which sweep ran"


def test_a_failed_config_still_counts_toward_the_shared_denominator():
    """The whole point of recording BEFORE running, preserved when sharing."""
    log = SearchSpaceLog(scope="universe-scan")

    def flaky(prices, fast=5, slow=20):
        if fast == 10:
            raise RuntimeError("boom")
        return ma_cross(prices, fast=fast, slow=slow)

    sweep(_ohlc(), flaky, {"fast": [5, 10]}, timeout=15, scope="a", search_log=log)
    tm = sweep(_ohlc(seed=2), flaky, {"fast": [5, 10]}, timeout=15, scope="b", search_log=log)
    assert tm.honest_n == 4          # 2 sweeps x 2 configs, including both failures
    assert tm.n_trials < tm.honest_n


# --- the semantics that bite -----------------------------------------------------------

def test_the_denominator_is_read_at_the_moment_it_is_asked_for():
    """Documented trap: a mid-scan verdict is priced for the search SO FAR, not the whole
    scan. Price the winner once the search is complete."""
    log = SearchSpaceLog(scope="universe-scan")
    first = _sweep(1, log, scope="mkt_a")
    mid = first.honest_n
    _sweep(2, log, scope="mkt_b")
    assert mid == 4 and first.honest_n == 8, \
        "honest_n must reflect the ledger now, not a value frozen at sweep time"
