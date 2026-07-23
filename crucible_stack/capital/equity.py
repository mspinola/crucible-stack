"""capital/equity.py — the R-log → equity curve seam (private sibling).

The capital sim is layer 2: it consumes a crucible `TradeLog` (per-trade R-multiples,
1R = entry→stop risk) plus a sizing/capital model and produces an `EquityResult` — the
currency equity path and its account-level stats. This is the first place currency
enters the toolchain; crucible stays capital-free, so the 1R↔currency mapping lives
HERE (recorded as `meta["r_denominator"]`), never upstream.

It is downstream of crucible's verdict: you only size an edge that already passed the
gauntlet. `EquityResult` is the seam this package owns (capital sim → orchestrator),
per docs/design/seam-contracts.md (Seam 3). crucible never imports it.

First cut: a pure numpy/pandas fixed-fractional sizer. A heavier engine (vectorbt /
RealTest / quantstats) can wrap behind the same `simulate_equity` signature later —
the seam is the contract, not the implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd
from crucible.edge import TradeLog

__all__ = ["EquityStats", "EquityResult", "simulate_equity"]


@dataclass(frozen=True)
class EquityStats:
    """Account-level summary of one equity path. All fractions, not percents."""
    cagr: float          # compound annual growth rate
    max_dd: float        # worst peak-to-trough, e.g. -0.23
    sharpe: float        # annualized, ddof=1 (crucible convention: 0.0 if no dispersion)
    sortino: float       # annualized, downside deviation
    exposure: float      # time-weighted fraction of the calendar spent in a position


@dataclass(frozen=True)
class EquityResult:
    """The capital-sim → orchestrator seam.

    Everything downstream (reporting, MC bands, deployment sizing) reads THIS, never the
    raw R-log — currency, costs and the sizing model are all baked in and recorded in
    `meta`. `trades` is in currency (contrast the TradeLog, which is in R).
    """
    equity: pd.Series          # index=exit time, account equity in `currency`
    returns: pd.Series         # per-trade account returns (equity.pct_change)
    trades: pd.DataFrame       # per-trade records IN CURRENCY
    stats: EquityStats
    meta: Mapping[str, object] = field(default_factory=dict)

    REQUIRED_META = ("starting_capital", "currency", "sizing_model",
                     "commission", "slippage", "r_denominator")

    def __post_init__(self):
        missing = [k for k in self.REQUIRED_META if k not in self.meta]
        if missing:
            raise ValueError(f"EquityResult.meta missing required keys: {missing}")


def simulate_equity(
    trade_log: TradeLog,
    *,
    starting_capital: float = 100_000.0,
    risk_pct: float = 0.01,
    sizes: Optional[Sequence[float]] = None,
    commission: float = 0.0,
    slippage_r: float = 0.0,
    currency: str = "USD",
    periods_per_year: float = 252.0,
) -> EquityResult:
    """Size a per-trade R-log into a currency equity curve.

    Default is fixed-fractional: each trade risks `risk_pct` of *current* equity, so a
    trade returning R multiples earns ``R × risk$`` in currency, minus `commission` (flat)
    and `slippage_r × risk$`. Pass `sizes` — a per-trade risk-fraction array **row-aligned
    to `trade_log.frame`** — to size each trade individually instead (e.g. the
    concurrency/correlation-aware fractions from `npf.validation.sizing.build_sizes`,
    policy 'corr_scaled'); `risk_pct` is then ignored and each trade uses its own fraction.
    The size travels with the trade (a `risk_frac` column on `trades`), so the MC bands can
    resample it. The 1R denominator rides the equity curve; `meta["r_denominator"]` records
    the *initial* 1R (the representative risk fraction × starting_capital).

    Requires the TradeLog to carry `exit_date` (the equity path is ordered/indexed by it).
    `entry_date` is used for the exposure stat when present.

    Parameters
    ----------
    periods_per_year : the annualization factor for sharpe/sortino. Returns are per-trade,
        annualized by the realized trades-per-year, so this only sets the calendar basis
        for CAGR/exposure day counts (252 = trading days).
    """
    df = trade_log.frame
    if "exit_date" not in df.columns:
        raise ValueError("simulate_equity needs exit_date on the TradeLog to order the "
                         "equity path (barrier_trades/simulate_rules both emit it)")
    if trade_log.n == 0:
        raise ValueError("simulate_equity got an empty TradeLog")

    # exit-date order (stable) — carry the per-trade sizes through the same permutation.
    sort_pos = np.argsort(pd.to_datetime(df["exit_date"]).to_numpy(), kind="stable")
    ordered = df.iloc[sort_pos].reset_index(drop=True)
    r = ordered["r"].to_numpy(dtype=float)
    exit_dates = pd.to_datetime(ordered["exit_date"])
    has_entry = "entry_date" in ordered.columns
    entry_dates = pd.to_datetime(ordered["entry_date"]) if has_entry else None

    if sizes is None:
        if not (0.0 < risk_pct < 1.0):
            raise ValueError(f"risk_pct must be in (0, 1), got {risk_pct}")
        sizes_ordered = np.full(len(r), float(risk_pct))
    else:
        sizes_arr = np.asarray(sizes, dtype=float)
        if sizes_arr.shape != (trade_log.n,):
            raise ValueError(f"sizes must be one risk fraction per trade "
                             f"(got {sizes_arr.shape}, need ({trade_log.n},))")
        if not np.isfinite(sizes_arr).all() or (sizes_arr < 0).any() or (sizes_arr >= 1).any():
            raise ValueError("sizes must be finite and in [0, 1)")
        if sizes_arr.sum() <= 0:
            raise ValueError("sizes are all zero — no risk deployed")
        sizes_ordered = sizes_arr[sort_pos]

    equity = float(starting_capital)
    rows = []
    for k in range(len(r)):
        risk_amt = sizes_ordered[k] * equity             # 1R in currency for this trade
        pnl = (r[k] - slippage_r) * risk_amt - commission
        equity += pnl
        rows.append({
            "entry_date": entry_dates.iloc[k] if has_entry else pd.NaT,
            "exit_date": exit_dates.iloc[k],
            "r": r[k],
            "risk_frac": sizes_ordered[k],
            "risk_amount": risk_amt,
            "pnl": pnl,
            "equity": equity,
        })
    trades = pd.DataFrame(rows)

    # equity path indexed by exit time, seeded with the pre-trade starting point so
    # pct_change / drawdown see the full run.
    eq_idx = pd.DatetimeIndex([exit_dates.iloc[0]]).append(pd.DatetimeIndex(exit_dates))
    eq_vals = np.concatenate([[starting_capital], trades["equity"].to_numpy()])
    equity_series = pd.Series(eq_vals, index=eq_idx, name="equity")
    returns = pd.Series(trades["pnl"].to_numpy() / eq_vals[:-1],
                        index=pd.DatetimeIndex(exit_dates), name="return")

    stats = _equity_stats(equity_series, returns, exit_dates, entry_dates,
                          periods_per_year)
    rep_risk = float(np.mean(sizes_ordered))             # representative per-trade fraction
    meta = {
        "starting_capital": float(starting_capital),
        "currency": currency,
        "sizing_model": "fixed_fractional" if sizes is None else "per_trade",
        "commission": float(commission),
        "slippage": float(slippage_r),
        "r_denominator": float(rep_risk * starting_capital),
        "risk_pct": rep_risk,                            # mean size when per-trade
        "n_trades": int(trade_log.n),
    }
    return EquityResult(equity=equity_series, returns=returns, trades=trades,
                        stats=stats, meta=meta)


def _annualized(returns: np.ndarray, exit_dates: pd.Series, periods_per_year: float):
    """Realized trades-per-year from the calendar span (for annualizing per-trade stats)."""
    if len(returns) < 2:
        return 0.0
    span_days = (exit_dates.iloc[-1] - exit_dates.iloc[0]).days
    if span_days <= 0:
        return float(len(returns))          # all same day — degenerate, avoid /0
    years = span_days / 365.25
    return len(returns) / years


def _equity_stats(equity: pd.Series, returns: pd.Series, exit_dates: pd.Series,
                  entry_dates, periods_per_year: float) -> EquityStats:
    r = returns.to_numpy(dtype=float)

    # CAGR from the equity endpoints over the calendar span.
    span_days = (equity.index[-1] - equity.index[0]).days
    years = span_days / 365.25 if span_days > 0 else 0.0
    if years > 0 and equity.iloc[0] > 0:
        cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0
    else:
        cagr = 0.0

    # Max drawdown: worst point on the underwater curve.
    running_peak = equity.cummax()
    drawdown = equity / running_peak - 1.0
    max_dd = float(drawdown.min())

    # Sharpe / Sortino on the per-trade return series, annualized by trades/year.
    # Guard dispersion with an epsilon scaled to the return magnitude: homogeneous
    # returns (e.g. every loss a clean -1R -> the identical -risk_pct) leave a
    # float-noise std ~1e-18 that would otherwise blow the ratio up to ~1e14.
    tpy = _annualized(r, exit_dates, periods_per_year)
    ann = np.sqrt(tpy) if tpy > 0 else 0.0
    eps = max(float(np.abs(r).mean()) * 1e-9, 1e-15)
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    sharpe = float(r.mean() / sd * ann) if sd > eps else 0.0      # crucible: 0.0, not NaN
    downside = r[r < 0.0]
    dd_std = downside.std(ddof=1) if len(downside) > 1 else 0.0
    if dd_std > eps:
        sortino = float(r.mean() / dd_std * ann)
    elif abs(float(r.mean())) <= eps:
        sortino = 0.0                        # no edge and no downside -> flat
    else:
        sortino = float("nan")               # positive edge, no downside dispersion:
        #                                      the ratio is unbounded, report it undefined

    # Exposure: time-weighted fraction of the calendar spent holding. Needs entry_date;
    # can exceed 1.0 when positions overlap (concurrent book), which is honest.
    if entry_dates is not None and span_days > 0:
        held = (exit_dates.reset_index(drop=True) -
                entry_dates.reset_index(drop=True)).dt.days.clip(lower=0).sum()
        total = (exit_dates.iloc[-1] - entry_dates.iloc[0]).days
        exposure = float(held / total) if total > 0 else float("nan")
    else:
        exposure = float("nan")

    return EquityStats(cagr=cagr, max_dd=max_dd, sharpe=sharpe,
                       sortino=sortino, exposure=exposure)
