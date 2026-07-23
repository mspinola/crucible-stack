"""select — pick the honest winner from a TrialMatrix and say whether to trust it.

The last arrow of the optimizer. Selecting by any in-sample metric is the naive
step; the honesty is entirely in the corrections that judge the pick:

  * pbo_cscv       did in-sample winning carry out-of-sample, or is the search overfit?
  * deflated_sharpe is the winner's Sharpe significant given how MANY configs were tried?
  * reality_check   is the winner's edge distinguishable from zero at all?

The winner is chosen by `objective` (default per-config Sharpe) and marked in the
ledger. `trustworthy` is the AND of the gates that could be computed.

**Each objective carries the correction that tests the statistic it selects on.**
Sharpe is often not what you deploy on — a risk-sized book lives on per-trade expectancy
and drawdown, and a filter that halves your trade count can raise expectancy while
lowering Sharpe (fewer, lumpier periods). But correcting a mean-based search with
`deflated_sharpe` would price a search over a statistic nobody selected on. So:

  * `objective="sharpe"`     -> `deflated_sharpe` (the sharper test for that statistic)
  * `objective="mean_r"`     -> `spa_test` on the per-period series
  * `objective="expectancy"` -> `spa_test` on the per-TRADE series

SPA (Hansen) is metric-agnostic: it takes the best variant's statistic and compares it
against the null distribution of the BEST across every variant tried, so taking the max
inside each permutation is what corrects for the search. `pbo_cscv` likewise accepts a
columnar `metric`, so "did in-sample winning carry out-of-sample" is asked about the same
quantity the winner was chosen by.

`coherent` therefore means *the correction tests the selected metric* — true for every
built-in objective. It is False only for a **custom callable**, where we cannot know what
the scorer optimizes, so the corrections stay on Sharpe and a reason says so loudly.

The deflation is priced off the ledger's `honest_n`, not the number of matrix
columns. Those coincide only when every config scored: a config that errored or was
too thin still cost you a look at the data, and a search sharing one ledger across
several sweeps was one search. Sharing a ledger (`sweep(search_log=...)`) is what
makes a universe scan cost what it should — otherwise every per-market correction is
blind to the other markets and the scan manufactures a winner.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple, Union

import numpy as np
import pandas as pd
from crucible.edge import reality_check
from crucible.edge.stats import Verdict
from crucible.validation import Thresholds, pbo_cscv, spa_test
from crucible.validation.pbo import deflated_sharpe

from crucible_stack.optimize.trial_matrix import TrialMatrix

__all__ = ["select", "Selection", "OBJECTIVES"]


@dataclass(frozen=True)
class Selection:
    """The honest winner of a search, with the corrections that judge it."""
    config_id: str
    params: dict
    honest_n: int                   # variants tried — the correction denominator
    trial_sharpe: float             # the winner's own per-config Sharpe
    pbo: float                      # P(best-IS config below OOS median); nan if uncomputable
    deflated_sharpe: float          # P(winner's true Sharpe clears the multiple-testing bar)
    reality: Optional[Verdict]      # reality_check on the winner's log, or None if not kept
    trustworthy: bool
    reasons: Tuple[str, ...]
    objective: str = "sharpe"        # what the winner was CHOSEN on
    objective_score: float = float("nan")
    coherent: bool = True            # True when the correction tests the selected metric
    correction: str = "deflated_sharpe"   # which multiple-testing correction ran
    spa_pvalue: float = float("nan")      # SPA corrected p-value; nan when SPA did not run

    def __repr__(self) -> str:
        r = self.reality.label if self.reality is not None else "n/a"
        pbo = "n/a" if np.isnan(self.pbo) else f"{self.pbo:.2f}"
        corr = (f"dsr={self.deflated_sharpe:.0%}" if self.correction == "deflated_sharpe"
                else f"spa_p={self.spa_pvalue:.4f}")
        return (f"Selection({self.config_id!r} sharpe={self.trial_sharpe:+.2f} "
                f"pbo={pbo} {corr} reality={r} "
                f"-> {'TRUSTWORTHY' if self.trustworthy else 'NOT trustworthy'})")


def _sharpe_scores(tm: TrialMatrix) -> pd.Series:
    """Per-config Sharpe — the metric the corrections themselves operate on."""
    return tm.trial_sharpes()


def _mean_r_scores(tm: TrialMatrix) -> pd.Series:
    """Mean R per PERIOD. Rewards trading often as well as trading well."""
    return tm.returns.mean(axis=0)


def _expectancy_scores(tm: TrialMatrix) -> pd.Series:
    """Mean R per TRADE — what a risk-sized book is actually deployed on.

    Needs the per-config trade logs (`sweep(keep_logs=True)`), because per-period
    returns cannot distinguish "traded twice as well" from "traded twice as often".
    """
    logs = tm.trade_logs or {}
    missing = [c for c in tm.returns.columns if c not in logs]
    if missing:
        raise ValueError(
            f"objective='expectancy' needs a TradeLog per config; {len(missing)} are "
            f"missing (e.g. {missing[:3]}). Re-run the sweep with keep_logs=True.")
    return pd.Series({c: float(np.mean(logs[c].r)) for c in tm.returns.columns})


def _mean_cols(block: np.ndarray) -> np.ndarray:
    """Per-column mean — the columnar metric matching a mean-based objective.

    `pbo_cscv` takes a pluggable `metric`, so PBO can rank IS-vs-OOS on the same
    statistic the search selected on instead of always ranking on Sharpe.
    """
    return block.mean(axis=0) if block.shape[0] else np.zeros(block.shape[1])


def _periodic_series(tm: TrialMatrix) -> dict:
    return {c: tm.returns[c].to_numpy(dtype=float) for c in tm.returns.columns}


def _per_trade_series(tm: TrialMatrix) -> dict:
    logs = tm.trade_logs or {}
    return {c: np.asarray(logs[c].r, dtype=float) for c in tm.returns.columns if c in logs}


# Each objective carries the correction that tests the SAME statistic it selects on.
#   score   : config -> score (what the winner is chosen by)
#   pbo     : columnar metric for pbo_cscv, so IS/OOS ranking matches the objective
#   spa     : series per variant for spa_test, or None to use deflated_sharpe
OBJECTIVES = {
    "sharpe":     {"score": _sharpe_scores,     "pbo": None,        "spa": None},
    "mean_r":     {"score": _mean_r_scores,     "pbo": _mean_cols,  "spa": _periodic_series},
    "expectancy": {"score": _expectancy_scores, "pbo": _mean_cols,  "spa": _per_trade_series},
}


def _safe_blocks(n_rows: int, want: int) -> int:
    """Largest even block count <= min(want, n_rows); 0 if too few rows for CSCV."""
    s = min(want, n_rows)
    s -= s % 2
    return s if s >= 2 else 0


def select(
    tm: TrialMatrix,
    *,
    objective: Union[str, Callable[[TrialMatrix], pd.Series]] = "sharpe",
    thresholds: Optional[Thresholds] = None,
    pbo_blocks: int = 16,
    mark: bool = True,
) -> Selection:
    """Pick the highest-Sharpe config, correct it for the search, and gate it.

    The two overfit bars — PBO and deflated Sharpe — live on crucible's central,
    overridable `Thresholds` (`max_pbo`, `min_deflated_sharpe`), the same home the
    gauntlet gates read from; pass a customized `thresholds` to retune both. Defaults
    to `Thresholds()` (max_pbo=0.5, min_deflated_sharpe=0.95).

    `trustworthy` is the AND of every gate that could run: PBO <= `thresholds.max_pbo`,
    deflated Sharpe >= `thresholds.min_deflated_sharpe`, and (if the winner's trade log
    was kept) reality_check == HELD. A gate that cannot be computed (e.g. PBO with too
    few periods) is skipped and noted in `reasons`, not silently passed.

    Marks the winner in the ledger via `mark_selected` (now an in-place update, so
    the variant count is not inflated) unless `mark=False`.
    """
    thr = thresholds if thresholds is not None else Thresholds()
    max_pbo, min_deflated_sharpe = thr.max_pbo, thr.min_deflated_sharpe

    if callable(objective):
        # A custom scorer: we cannot know which statistic it optimizes, so the
        # corrections stay on Sharpe and `coherent` reports the mismatch honestly.
        obj_name, spec = getattr(objective, "__name__", "custom"), {
            "score": objective, "pbo": None, "spa": None}
    else:
        if objective not in OBJECTIVES:
            raise ValueError(
                f"unknown objective {objective!r}; choose from {sorted(OBJECTIVES)} "
                "or pass a callable (TrialMatrix) -> Series of per-config scores")
        obj_name, spec = objective, OBJECTIVES[objective]

    scores = spec["score"](tm)
    sharpes = tm.trial_sharpes()
    winner = scores.idxmax()
    winner_sharpe = float(sharpes[winner])

    # PBO ranks IS vs OOS by a columnar metric. Point it at the statistic the search
    # actually selected on, so "did in-sample winning carry out-of-sample" is asked
    # about the same quantity the winner was chosen by.
    S = _safe_blocks(tm.returns.shape[0], pbo_blocks)
    pbo_kw = {"metric": spec["pbo"]} if spec["pbo"] is not None else {}
    pbo = (float(pbo_cscv(tm.returns, S=S, **pbo_kw).pbo)
           if (tm.n_trials >= 2 and S) else float("nan"))
    # The correction denominator is the LEDGER's count, not the matrix column count.
    # `honest_n` sets how many configs were tried; they differ whenever a config errored
    # or was too thin to score, and whenever one search spans several sweeps. `honest_n`
    # raises rather than guessing when no ledger is attached, which is the correct
    # refusal: an unknown search size cannot be corrected for.
    #
    # WHICH correction runs depends on what the search selected on. deflated_sharpe
    # deflates a SHARPE against the expected max Sharpe of N noise trials, so it is the
    # right (and sharper) test only when Sharpe is the objective. For a mean-based
    # objective it would price a search over a statistic nobody selected on, so the
    # correction routes to SPA instead: a permutation test that takes the best variant's
    # statistic and compares it against the null distribution of the BEST across every
    # variant tried. Taking the max inside each permutation is what corrects for the
    # search, and it assumes nothing about Sharpe.
    dsr, spa_p, correction = float("nan"), float("nan"), "deflated_sharpe"
    if spec["spa"] is None:
        dsr = float(deflated_sharpe(sharpes.to_numpy(), returns=tm.returns_for(winner),
                                    n_trials=tm.honest_n).deflated_sharpe)
    else:
        series = spec["spa"](tm)
        if len(series) < 2:
            raise ValueError(
                f"objective={obj_name!r} corrects with SPA, which needs the series of at "
                f"least 2 variants; got {len(series)}. For 'expectancy' this usually means "
                "the sweep ran with keep_logs=False.")
        res = spa_test(series, seed=thr.seed)
        spa_p, correction = float(res["corrected_pvalue"]), "spa"

    log = tm.log_for(winner)
    reality = reality_check(log) if log is not None else None

    if mark and tm.search_log is not None:
        tm.search_log.mark_selected(tm.configs[winner], score=winner_sharpe)

    # Coherent means the correction tests the statistic the winner was selected on —
    # not that the objective happened to agree with Sharpe. A built-in objective is
    # coherent by construction now that each carries its own correction; a custom
    # callable cannot be, because we cannot know what it optimizes.
    coherent = spec["spa"] is not None or obj_name == "sharpe"

    gates, reasons = [], []
    if not coherent:
        reasons.append(
            f"objective '{obj_name}' is a custom scorer, so the corrections stayed on "
            f"Sharpe (whose best is {sharpes.idxmax()!r}) — selection and correction are "
            "NOT the same metric; read the verdict with that in mind")

    if np.isnan(pbo):
        reasons.append("pbo n/a (too few periods for CSCV)")
    else:
        ok = pbo <= max_pbo
        gates.append(ok)
        reasons.append(f"pbo {'ok' if ok else 'too high'} ({pbo:.2f} vs <= {max_pbo})")

    if correction == "spa":
        ok = spa_p < thr.alpha
        gates.append(ok)
        reasons.append(f"spa {'ok' if ok else 'above bar'} (p={spa_p:.4f} vs < "
                       f"{thr.alpha}) — corrected on '{obj_name}', the metric selected on")
    else:
        ok = dsr >= min_deflated_sharpe
        gates.append(ok)
        reasons.append(f"deflated_sharpe {'ok' if ok else 'below bar'} "
                       f"({dsr:.0%} vs >= {min_deflated_sharpe:.0%})")

    if reality is not None:
        ok = reality.label == "HELD"
        gates.append(ok)
        reasons.append(f"reality {reality.label}")

    return Selection(
        config_id=winner, params=dict(tm.configs[winner]), honest_n=tm.honest_n,
        trial_sharpe=winner_sharpe, pbo=pbo, deflated_sharpe=dsr, reality=reality,
        trustworthy=bool(gates) and all(gates), reasons=tuple(reasons),
        objective=obj_name, objective_score=float(scores[winner]), coherent=coherent,
        correction=correction, spa_pvalue=spa_p,
    )
