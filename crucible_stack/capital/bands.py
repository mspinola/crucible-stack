"""capital/bands.py — block-aware Monte-Carlo bands on the equity curve.

`simulate_equity` gives you the one equity path the trades actually produced.
`equity_bands` asks the honest follow-up: how lucky was that path? — and answers it with
the **concurrency-correct** portfolio Monte Carlo, not a per-trade shuffle.

It aggregates the book to a **monthly portfolio-return series** (r_month = Σ of each
trade's sized R exiting that month), so correlated positions closing together survive as
one big move — the clustering a per-trade resample throws away. Then it circular-block-
bootstraps that monthly series (contiguous blocks preserve within-block autocorrelation)
into a distribution of equity paths, and reads off the percentile envelope + the terminal
/ max-drawdown distributions.

This is the single shared engine: the monthly aggregation + block bootstrap live in
crucible_stack.framework.montecarlo; equity_bands is the currency-curve / fan front-end over it.
Bands are indexed by **calendar month**, so the fan lines up with the _account_equity_plot
curve. Read them WITH crucible's verdict — a wide, deep envelope on a FRAGILE edge is the
honest picture, not a forecast.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd
from crucible.edge import TradeLog

from crucible_stack.capital.equity import EquityResult, simulate_equity
from crucible_stack.framework.montecarlo import (
    block_bootstrap_paths,
    monthly_returns,
    weighted_monthly_returns,
)

__all__ = ["EquityBands", "equity_bands"]


@dataclass(frozen=True)
class EquityBands:
    """Monte-Carlo envelope around a sized equity curve.

    `bands` is indexed by calendar date (row 0 = starting capital, then month-ends), one
    column per requested percentile ('p5', 'p50', …), values in currency. `terminal` and
    `max_dd` are the per-sim distributions behind the envelope; `observed` is the actual
    realized per-trade curve for overlay. Read the bands WITH crucible's verdict, never as
    a forecast — a wide, deep envelope on a FRAGILE edge is the honest picture, not a promise.
    """
    bands: pd.DataFrame            # index=date, columns=p{pct}; account equity (currency)
    terminal: np.ndarray           # (n_sims,) terminal equity across resamples
    max_dd: np.ndarray             # (n_sims,) worst drawdown per resample (fraction, ≤ 0)
    observed: EquityResult         # the real path the trades produced (unresampled)
    meta: Mapping[str, object] = field(default_factory=dict)

    def terminal_ci(self, lo: float = 5, hi: float = 95) -> tuple[float, float]:
        """Percentile CI of terminal equity, e.g. the 5–95 band on final account value."""
        return float(np.percentile(self.terminal, lo)), float(np.percentile(self.terminal, hi))

    def max_dd_ci(self, lo: float = 5, hi: float = 95) -> tuple[float, float]:
        """Percentile CI of max drawdown (both ≤ 0; hi is the shallower, lo the deeper)."""
        return float(np.percentile(self.max_dd, lo)), float(np.percentile(self.max_dd, hi))


def equity_bands(
    trade_log: TradeLog,
    *,
    starting_capital: float = 100_000.0,
    risk_pct: float = 0.01,
    sizes: Optional[Sequence[float]] = None,
    commission: float = 0.0,
    slippage_r: float = 0.0,
    currency: str = "USD",
    n_sims: int = 1_000,
    block: int = 6,
    percentiles: Sequence[float] = (5, 25, 50, 75, 95),
    seed: int = 0,
) -> EquityBands:
    """Concurrency-correct block-bootstrap Monte-Carlo bands around the equity curve.

    Aggregates the book into a monthly portfolio-return series (correlated trades exiting
    together become one move) and circular-block-bootstraps it `n_sims` times into a
    distribution of currency equity paths → the percentile envelope + terminal / max-DD
    distributions. `block` is in **months** (the MC engine's unit; ~1 ≈ i.i.d. months,
    larger absorbs more month-to-month autocorrelation). Pass `sizes` (per-trade risk
    fractions, row-aligned to `trade_log.frame`, e.g. corr_scaled) to size each trade
    individually; else every trade risks `risk_pct`. `observed` is the realized per-trade
    curve (for overlay + stats). Commission/slippage apply to the realized curve only;
    the monthly-MC basis is Σ sized-R per month (a realized-equity lower bound on DD).
    """
    if trade_log.n == 0:
        raise ValueError("equity_bands got an empty TradeLog")
    df = trade_log.frame
    if "exit_date" not in df.columns:
        raise ValueError("equity_bands needs exit_date on the TradeLog (as simulate_equity does)")
    if n_sims < 1:
        raise ValueError(f"n_sims must be >= 1, got {n_sims}")

    # the realized per-trade curve — validates risk_pct/sizes, and is the overlay line + stats
    observed = simulate_equity(trade_log, starting_capital=starting_capital, risk_pct=risk_pct,
                               sizes=sizes, commission=commission, slippage_r=slippage_r,
                               currency=currency)

    # concurrency-correct monthly portfolio-return series (framework MC engine)
    d = df.copy()
    d["pct_return"] = d["r"]                                  # montecarlo / sizing schema
    if sizes is None:
        rmonth = monthly_returns(d, risk_pct)
    else:
        rmonth = weighted_monthly_returns(d, np.asarray(sizes, dtype=float))

    ym = pd.to_datetime(d["exit_date"]).dt.to_period("M")
    grid = pd.period_range(ym.min(), ym.max(), freq="M")     # same grid the series is on
    n_months = len(grid)
    if n_months < 2:
        raise ValueError("equity_bands needs the book to span >= 2 months for a monthly MC")

    mult = block_bootstrap_paths(rmonth, block, n_sims, seed)  # (n_sims, n_months) equity multiple
    eq = np.empty((n_sims, n_months + 1), dtype=float)
    eq[:, 0] = starting_capital                              # seed month 0 at starting capital
    eq[:, 1:] = starting_capital * mult

    pcts = sorted(float(p) for p in percentiles)
    index = pd.DatetimeIndex([grid[0].to_timestamp(how="start")]).append(grid.to_timestamp(how="end"))
    bands = pd.DataFrame(np.percentile(eq, pcts, axis=0).T, index=index,
                         columns=[f"p{int(round(p))}" for p in pcts])
    bands.index.name = "date"

    peak = np.maximum.accumulate(eq, axis=1)
    max_dd = (eq / peak - 1.0).min(axis=1)                   # (n_sims,) ≤ 0
    terminal = eq[:, -1]

    meta = {
        "n_sims": int(n_sims),
        "block": int(max(block, 1)),
        "block_unit": "months",
        "n_months": int(n_months),
        "n_trades": int(trade_log.n),
        "percentiles": [int(round(p)) for p in pcts],
        "sizing_model": observed.meta["sizing_model"],
        "starting_capital": float(starting_capital),
        "risk_pct": observed.meta["risk_pct"],              # mean fraction when per-trade
        "currency": currency,
        "seed": int(seed),
        "engine": "montecarlo.monthly",
    }
    return EquityBands(bands=bands, terminal=terminal, max_dd=max_dd, observed=observed, meta=meta)
