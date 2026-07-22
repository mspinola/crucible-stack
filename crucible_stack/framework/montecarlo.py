"""montecarlo — the block-bootstrap portfolio Monte Carlo.

The single MC engine behind the capital layer's equity bands and the orchestrator's
drift envelope: aggregate a book to a monthly return series, resample it in contiguous
blocks so within-block clustering survives, and read the drawdown / terminal
distribution off the resampled paths.

Moved out of `npf.validation` per ADR-0004 — it is engine machinery with no strategy
in it, and it was only ever there because that is where it was written. **Computation
only**: the risk-fraction sweep that PRINTS a drawdown/ruin table stayed behind in
`npf.validation.portfolio_mc.run`, following the same split ADR-0002 drew for crucible
(a pure core; reporting separate).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = ["monthly_returns", "weighted_monthly_returns", "max_drawdown",
           "block_index", "block_bootstrap", "block_bootstrap_paths"]


def monthly_returns(trades: pd.DataFrame, risk_frac: float) -> np.ndarray:
    """Monthly account-return series at per-position risk `risk_frac` (fraction of equity risked
    per trade). r_month = risk_frac · Σ(R exiting that month), reindexed to a gap-free monthly grid."""
    d = trades.copy()
    d['exit_date'] = pd.to_datetime(d['exit_date'])
    d['ym'] = d['exit_date'].dt.to_period('M')
    m = d.groupby('ym')['pct_return'].sum()
    grid = pd.period_range(m.index.min(), m.index.max(), freq='M')
    m = m.reindex(grid, fill_value=0.0)
    return risk_frac * m.values.astype(float)


def max_drawdown(r: np.ndarray) -> float:
    """Max peak-to-trough drawdown of the compounded equity curve, as a fraction (negative)."""
    eq = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(eq)
    return float((eq / peak - 1.0).min())


def block_index(rng: np.random.Generator, n: int, block: int) -> np.ndarray:
    """One circular block-bootstrap resample of the positions 0..n-1, length n.

    The block mechanics themselves — contiguous blocks preserve within-block clustering /
    autocorrelation, block order is randomized, wrap-around keeps every position equally
    likely. Split out so that callers accumulating something *other* than compounded
    returns (e.g. additive R in crucible_stack.orchestrate.drift) reuse this one resampler instead of
    growing a second bootstrap. Consumes exactly one `rng.integers` call per resample.
    """
    nblocks = int(np.ceil(n / block))
    starts = rng.integers(0, n, nblocks)
    return np.concatenate([(np.arange(s, s + block) % n) for s in starts])[:n]


def block_bootstrap_paths(r: np.ndarray, block: int, n_sims: int, seed: int = 7) -> np.ndarray:
    """(n_sims, len(r)) compounded equity-multiple paths from a circular block bootstrap of
    the monthly return series `r` — contiguous blocks preserve within-block clustering /
    autocorrelation, block order is randomized. Each row is ``cumprod(1 + resampled r)``.
    The distribution primitive under both `block_bootstrap` and crucible_stack.capital.equity_bands.

    NOTE the accumulation is MULTIPLICATIVE, which assumes `r` are fractional returns.
    R-multiples add rather than compound, so an R-space caller must not use this."""
    rng = np.random.default_rng(seed)
    n = len(r)
    paths = np.empty((n_sims, n))
    for i in range(n_sims):
        paths[i] = np.cumprod(1.0 + r[block_index(rng, n, block)])
    return paths


def block_bootstrap(r: np.ndarray, block: int, n_sims: int, seed: int = 7):
    """Circular block bootstrap of the monthly return series. Returns arrays of (maxDD, terminal
    equity multiple) over `n_sims` synthetic histories of the same length."""
    paths = block_bootstrap_paths(r, block, n_sims, seed)
    peak = np.maximum.accumulate(paths, axis=1)
    return (paths / peak - 1.0).min(axis=1), paths[:, -1]


def weighted_monthly_returns(trades: pd.DataFrame, sizes: np.ndarray) -> np.ndarray:
    """Monthly account return = Sum(size_i * R_i) over trades EXITING that month,
    on a gap-free monthly grid — the sized analogue of portfolio_mc.monthly_returns."""
    d = trades.copy()
    d['exit_date'] = pd.to_datetime(d['exit_date'])
    d['contrib'] = sizes * d['pct_return'].values
    d['ym'] = d['exit_date'].dt.to_period('M')
    m = d.groupby('ym')['contrib'].sum()
    grid = pd.period_range(m.index.min(), m.index.max(), freq='M')
    return m.reindex(grid, fill_value=0.0).values.astype(float)


# Kept because a downstream package imported this private name before the API surface was
# defined. Private-by-underscore is a convention, not a barrier, and once something is
# actually depended upon the honest move is to name it public rather than to pretend.
_max_drawdown = max_drawdown
