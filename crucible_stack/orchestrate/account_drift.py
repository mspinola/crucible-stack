"""account_drift — the currency-side shell over the capital-free drift core.

ADR-0003 action item 3b. This module is the thin adapter between the account world
(`TradeLog`s from a live book, `EquityResult` / `EquityBands` from a provisioning run) and
`crucible_stack.orchestrate.drift`, which knows only about R.

**What this shell does NOT do is apply per-trade sizing.** That looks like an omission and
is the central design choice: R is already risk-normalized, so a comparison in R-space asks
*"is the edge behaving the way the sim predicted?"* — and that question is robust to the
account's sizing changing underneath it. Provision at 0.5% risk, trade live at 1%, and the
R-space comparison remains valid. Applying `sizes` here would re-couple the answer to
capital and quietly turn edge-drift into account-drift.

Account-drift (did the *equity curve* leave its currency band?) is a different question, and
`crucible_stack.capital.equity_bands` already answers it. This monitor is deliberately the edge question,
because that is the one that should trigger a re-optimization: an account can leave its band
purely by being sized differently, which is not news about the edge.

**Where the 1R denominator enters.** Only in `in_currency`, and only for human-facing
reporting. It never touches the decision path — `check_account_drift` returns the same
verdict whether or not anyone ever asks for currency. Seam 3 owns that number
(`EquityResult.meta["r_denominator"]`); this module cites it and does not re-derive it.

**Period granularity is load-bearing** and is the third trap in this design. An envelope
provisioned on monthly R compared against per-trade or weekly live R is nonsense that will
happily produce a number. So the envelope records the period it was built at, and
`check_account_drift` refuses to compare across a mismatch rather than trusting the caller.
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from crucible.edge import TradeLog

from crucible_stack.orchestrate.drift import (
    DEFAULT_LEVELS, DriftEnvelope, DriftVerdict, check_drift, envelope_from_r,
)
from crucible_stack.framework.montecarlo import monthly_returns

__all__ = ["MONTHLY", "monthly_r", "provision_envelope", "check_account_drift", "in_currency"]

MONTHLY = "M"


def monthly_r(trade_log: TradeLog) -> np.ndarray:
    """Concurrency-correct monthly Σ R for a book — capital-free.

    The same aggregation `equity_bands` performs, at `risk_frac=1.0` so no capital
    assumption enters: trades exiting in the same month sum into one move (correlated
    exits are one event, not many), on a gap-free monthly grid.
    """
    if trade_log.n == 0:
        return np.zeros(0, dtype=float)
    df = trade_log.frame
    if "exit_date" not in df.columns:
        raise ValueError(
            "drift monitoring needs exit_date on the TradeLog (as equity_bands does); "
            "without it, trades cannot be placed on a period grid")
    d = df.copy()
    d["pct_return"] = d["r"]                     # montecarlo / sizing schema
    return monthly_returns(d, 1.0)               # 1.0 => pure Σ R, no capital assumption


def provision_envelope(
    trade_log: TradeLog,
    *,
    block: int = 6,
    n_sims: int = 1_000,
    seed: int = 0,
    levels: Sequence[float] = DEFAULT_LEVELS,
    meta: Optional[Mapping[str, object]] = None,
) -> DriftEnvelope:
    """Freeze the envelope for a book at the moment it is promoted.

    Call this ONCE, when the gate promotes, and persist the result on the
    `DeploymentLedger` entry (Seam 4). Calling it again at comparison time is the
    re-baselining bug the core is shaped to prevent.

    `block` is in months, matching `equity_bands` / `framework.montecarlo`.
    """
    r = monthly_r(trade_log)
    if r.size < 2:
        raise ValueError(
            f"provisioning needs a book spanning >= 2 months, got {r.size}; "
            "a one-month envelope cannot describe a horizon")
    return envelope_from_r(
        r, block=block, n_sims=n_sims, seed=seed, levels=levels,
        meta={"period": MONTHLY, "n_trades": int(trade_log.n),
              "provisioned_months": int(r.size), **(dict(meta) if meta else {})},
    )


def check_account_drift(
    envelope: DriftEnvelope,
    live_log: TradeLog,
    *,
    breach_level: Optional[float] = None,
) -> DriftVerdict:
    """Has the live book left the envelope it was provisioned with?

    Aggregates the live trades with exactly the same monthly Σ R the envelope was built
    from, then defers to the capital-free core. No currency is consulted; no envelope is
    rebuilt.
    """
    period = envelope.meta.get("period")
    if period != MONTHLY:
        raise ValueError(
            f"envelope was built at period {period!r}, but live R is aggregated monthly "
            f"({MONTHLY!r}). Comparing across a granularity mismatch produces a number "
            "with no meaning — re-provision the envelope at the matching period.")
    return check_drift(envelope, monthly_r(live_log), breach_level=breach_level)


def in_currency(
    verdict: DriftVerdict,
    *,
    r_denominator: float,
    currency: str = "USD",
) -> Mapping[str, object]:
    """Restate a verdict's R quantities in currency, for humans. REPORT ONLY.

    Nothing here feeds a decision — `check_account_drift` has already returned the same
    verdict regardless. `r_denominator` is the 1R↔currency mapping owned by Seam 3
    (`EquityResult.meta["r_denominator"]`); pass that value rather than inventing one.
    """
    if not np.isfinite(r_denominator) or r_denominator <= 0:
        raise ValueError(f"r_denominator must be finite and positive, got {r_denominator}")
    k = float(r_denominator)
    return {
        "currency": currency,
        "r_denominator": k,
        "drifted": verdict.drifted,
        "elapsed": verdict.elapsed,
        "cum": verdict.cum_r * k,
        "cum_floor": verdict.cum_r_floor * k,
        "max_dd": verdict.max_dd * k,
        "max_dd_floor": verdict.max_dd_floor * k,
        "breaches": verdict.breaches,
        "note": "presentation only; the drift decision is made capital-free in R",
    }
