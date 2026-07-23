"""sweep — the plain-loop optimizer driver: strategy + small grid -> TrialMatrix.

No engine. `itertools.product` over a deliberately small grid; each config runs
`crucible.barrier_trades` (one faithful simulator, native R, capital-free), and its
per-trade R aggregates to a periodic (monthly) R series — one column of the matrix.

Two honesty behaviours are wired in:
  * Every config is recorded into a SearchSpaceLog BEFORE it runs, so a config that
    errors or produces no trades still counts toward `honest_n` (the correction
    denominator) even though it never becomes a matrix column.
  * The grid is capped (`MAX_CONFIGS`): the honest objective (PBO / deflated Sharpe)
    discounts large searches into oblivion, so a big grid is a bug, not a feature.
  * A ledger can be SHARED across sweeps (`search_log=`), so a search spanning many books
    or markets is priced as one search. Scanning a universe and keeping the best is a
    search over the universe, and a per-market denominator cannot see it.

See ADR-0001 (amendment) for why the sweep uses barrier_trades, not the vbt engine.
"""
from __future__ import annotations

import itertools
from typing import Callable, Mapping, Optional, Sequence

import pandas as pd
from crucible.edge import barrier_trades
from crucible.validation import SearchSpaceLog

from crucible_stack.optimize.trial_matrix import TrialMatrix

Strategy = Callable[..., pd.Series]   # (prices, **params) -> boolean entry Series
# A simulator turns a config's entries into a crucible TradeLog. It receives the
# varied config too, so simulators with config-dependent exits (an exit whose
# behaviour depends on the config being swept) can adapt; simple ones ignore it.
Simulator = Callable[..., "object"]   # (prices, entries, side, config) -> TradeLog

MAX_CONFIGS = 64   # keep the search space small — see module docstring


def _barrier_sim(tp: float, sl: float, timeout: int) -> Simulator:
    """The default simulator: crucible.barrier_trades with fixed ATR barriers.
    One faithful sim, native R, capital-free. Ignores the per-config dict."""
    def sim(prices, entries, side, config):
        return barrier_trades(prices, entries, side=side, tp=tp, sl=sl, timeout=timeout)
    sim.name = "barrier_trades"
    return sim


def _grid(param_grid: Mapping[str, Sequence]) -> list[dict]:
    if not param_grid:
        return [{}]
    keys = list(param_grid)
    return [dict(zip(keys, combo))
            for combo in itertools.product(*(param_grid[k] for k in keys))]


def _config_id(varied: dict) -> str:
    return "·".join(f"{k}={v}" for k, v in sorted(varied.items())) or "base"


def _periodic_r(tl, freq: str) -> pd.Series:
    """Per-trade R summed into calendar periods, indexed by exit date."""
    if tl.n == 0:
        return pd.Series(dtype=float)
    s = pd.Series(tl.r, index=pd.to_datetime(tl.frame["exit_date"]))
    return s.groupby(pd.Grouper(freq=freq)).sum()


def sweep(
    prices: pd.DataFrame,
    strategy: Strategy,
    param_grid: Mapping[str, Sequence],
    *,
    side: str = "long",
    fixed: Optional[Mapping[str, object]] = None,
    simulate: Optional[Simulator] = None,
    tp: float = 2.0,
    sl: float = 1.0,
    timeout: int = 20,
    freq: str = "ME",
    objective: str = "expectancy",
    scope: str = "sweep",
    keep_logs: bool = True,
    search_log: Optional[SearchSpaceLog] = None,
) -> TrialMatrix:
    """Run `strategy` across `param_grid` on `prices` and return a `TrialMatrix`.
    Capital-free; every value is R.

    `search_log` shares ONE ledger across several sweeps, so a search that spans multiple
    books or markets is priced as the single search it actually is. Without it each sweep
    opens its own ledger and `honest_n` counts only that sweep's grid — so scanning 45
    markets and keeping the best leaves every per-market correction blind to the other 44,
    which is how a scan manufactures a winner. Entries record `sweep_scope` so they stay
    attributable to the sweep that produced them.

    **Read the denominator when the search is finished, not during it.** `honest_n` is the
    ledger's count *at the moment it is read*, so a `select` called after sweep 3 of 45 is
    corrected for three sweeps, not forty-five. Price the winner once the whole search is
    complete; a mid-scan verdict is not wrong so much as premature.

    `simulate` is the pluggable simulator `(prices, entries, side, config) ->
    TradeLog`. Default: `crucible.barrier_trades` with `tp`/`sl`/`timeout` (generic
    ATR barriers). Pass a rules simulator (see `crucible_stack.optimize.simulators`) to fill
    the matrix under a strategy's REAL exits instead, rather than generic barriers.
    Whatever fills it, the seam and the verdict are identical.

    A config that raises or produces no trades is still recorded on the ledger
    (counted in `honest_n`) but does not become a matrix column (`n_trials`).
    """
    combos = _grid(param_grid)
    if len(combos) > MAX_CONFIGS:
        raise ValueError(
            f"grid expands to {len(combos)} configs > MAX_CONFIGS={MAX_CONFIGS}. "
            "The honest objective punishes large searches — narrow the grid to the "
            "few params you actually mean to tune.")

    sim = simulate if simulate is not None else _barrier_sim(tp, sl, timeout)
    engine = getattr(sim, "name", "custom")

    held = dict(fixed or {})
    held["side"] = side
    if simulate is None:                       # barrier params only describe the default sim
        held.update({"tp": tp, "sl": sl, "timeout": timeout})
    log = search_log if search_log is not None else SearchSpaceLog(scope=scope)

    trials, logs = {}, {}
    for varied in combos:
        # `sweep_scope` keeps entries attributable when several sweeps share one ledger
        log.record(varied, status="tried", fixed=held, sweep_scope=scope)
        cid = _config_id(varied)
        try:
            entries = strategy(prices, **varied)
            tl = sim(prices, entries, side, varied)
        except Exception:
            continue                                      # tried, on the ledger; no column
        r = _periodic_r(tl, freq)
        if r.empty:
            continue                                      # tried; produced nothing to score
        trials[cid] = (varied, r)
        if keep_logs:
            logs[cid] = tl

    if not trials:
        raise ValueError("no config produced any trades to score")

    return TrialMatrix.from_trials(
        trials,
        objective=objective,
        fixed=held,
        meta={"engine": engine, "search_space": dict(param_grid)},
        trade_logs=logs if keep_logs else None,
        search_log=log,
    )
