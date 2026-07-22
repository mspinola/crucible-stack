"""Pluggable simulators for `sweep`.

The default (ATR barriers via crucible.barrier_trades) lives in sweep.py. This
adds a rules simulator that fills the matrix via the faithful `simulate_rules`
(structural stops, registered exits, gap-honest fills), so a rules strategy can be swept
under its REAL exits, not just generic barriers — the difference between "does the
setup have an edge under fixed barriers" and "does the deployed strategy have one".

`simulate_rules` is imported lazily inside the factory so `crucible_stack.optimize` stays
capital-free and core-free at import time (importing it pulls the cotmetrics data
chain, which the seam and the sweep core do not need).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional, Tuple

import pandas as pd

from crucible.edge import TradeLog


def rules_simulator(
    *,
    is_equity: bool,
    stop_spec: Optional[Mapping[str, Any]] = None,
    exit_spec: Optional[Mapping[str, Any]] = None,
    spec_fn: Optional[Callable[[dict], Tuple[Mapping, Mapping]]] = None,
    entry_fill: str = "next_open",
    cost_r: float = 0.0,
    symbol: str = "",
    asset_class: str = "Unknown",
):
    """A sweep-compatible simulator that runs `simulate_rules` and returns a
    crucible `TradeLog`.

    The sweep's `entries` (= setup & trigger, already combined) are fed as the
    setup mask with an all-True trigger, so `simulate_rules` enters exactly on
    `entries` and applies the given exits.

    Exits: pass fixed `stop_spec` + `exit_spec`, OR a `spec_fn(config) ->
    (stop_spec, exit_spec)` for per-config exits (e.g. a gate-matched
    cot_neutral, where the ride depends on the swept gate). `spec_fn` wins if given.
    """
    if spec_fn is None and (stop_spec is None or exit_spec is None):
        raise ValueError("provide either spec_fn or both stop_spec and exit_spec")

    from crucible_stack.engine.simulator import simulate_rules   # lazy: keep crucible_stack.optimize core-free

    def sim(prices: pd.DataFrame, entries: pd.Series, side, config: dict):
        ss, es = spec_fn(config) if spec_fn is not None else (stop_spec, exit_spec)
        direction = "long" if side in ("long", 1) else "short"
        trig = pd.Series(True, index=prices.index)
        df = simulate_rules(
            prices, direction, is_equity, entries, trig, ss, es,
            entry_fill=entry_fill, symbol=symbol, asset_class=asset_class, cost_r=cost_r,
        )
        # simulate_rules' `pct_return` column holds the R-multiple; map it onto the
        # crucible schema (mfe/mae/bars_held/entry_date/exit_date already match).
        return TradeLog.from_frame(df, mapping={"pct_return": "r", "prob_success": "prob"})

    sim.name = "simulate_rules"
    return sim
