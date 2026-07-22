"""TrialMatrix — the optimizer -> crucible seam.

Every configuration a parameter search evaluated, as a periods x configs return
matrix plus the params that produced each column. This frame *is* the input
crucible's `pbo_cscv` (CSCV overfit probability) and `deflated_sharpe` consume.

The optimizer owns this type; crucible does not import it — it takes the raw
`.returns` DataFrame. That one-directional dependency (npf -> crucible) is what
keeps crucible pure. See docs/design/seam-contracts.md, Seam 1.

Two honesty invariants are baked into the type:

  1. Record what was held FIXED, not just what varied (`fixed`). A search over
     `{"fast": [10,20]}` against `ma_cross(df, fast=20, slow=50, kind="sma")`
     silently pins slow/kind at their defaults; without `fixed`, "what did this
     search actually explore?" is unanswerable from the artifact.

  2. `n_trials` is NOT the multiple-testing denominator. It counts columns —
     configs that produced a return. The honest denominator is `honest_n`, read
     from the attached SearchSpaceLog, which also counts variants that were tried
     but failed to score. Substituting n_trials undercounts the search and
     flatters every correction — the exact data-mining hole the ledger closes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from crucible.edge import TradeLog
from crucible.validation import SearchSpaceLog


@dataclass(frozen=True)
class TrialMatrix:
    """A parameter search's result, ready to hand to crucible.

    returns    : periods (DatetimeIndex) x config_id. Cells are that config's
                 periodic return in a CONSISTENT unit — summed R per period is the
                 crucible-native choice. This frame is the CSCV / PBO input.
    configs    : config_id -> the params that VARIED to produce that column.
    objective  : name of the score the search selected on (e.g. "deflated_sharpe").
    fixed      : params held CONSTANT across the search (the audit trail of what
                 was NOT explored — strategy defaults, barrier tp/sl/timeout, ...).
    trade_logs : optional config_id -> TradeLog, kept for the winner(s) so
                 `crucible.reality_check` / a tearsheet can run on them. Storing one
                 per config is usually wasteful.
    search_log : the SearchSpaceLog the sweep recorded into — the source of the
                 honest multiple-testing N (`honest_n`). Optional at construction,
                 but `honest_n` refuses to guess without it.
    meta       : provenance; must carry at least `engine` and `search_space`.
    """
    returns: pd.DataFrame
    configs: Mapping[str, dict]
    objective: str
    fixed: Mapping[str, object]
    trade_logs: Optional[Mapping[str, TradeLog]] = None
    search_log: Optional[SearchSpaceLog] = None
    meta: Mapping[str, object] = field(default_factory=dict)

    REQUIRED_META = ("engine", "search_space")

    def __post_init__(self) -> None:
        if self.returns.shape[0] == 0 or self.returns.shape[1] == 0:
            raise ValueError("returns must be non-empty (>=1 period, >=1 config)")
        if set(self.returns.columns) != set(self.configs):
            raise ValueError(
                f"returns columns {sorted(map(str, self.returns.columns))} and configs "
                f"keys {sorted(map(str, self.configs))} must match 1:1")
        if not isinstance(self.returns.index, pd.DatetimeIndex):
            raise ValueError(
                "returns must be indexed by period (DatetimeIndex); aggregate "
                "per-trade R into a periodic series (e.g. monthly summed R) first")
        missing = [k for k in self.REQUIRED_META if k not in self.meta]
        if missing:
            raise ValueError(
                f"meta missing required provenance {missing}; record the engine "
                "that produced the sweep and the search space it covered")
        if self.trade_logs is not None:
            stray = set(self.trade_logs) - set(self.configs)
            if stray:
                raise ValueError(
                    f"trade_logs has keys absent from configs: {sorted(map(str, stray))}")

    # ── constructor ──────────────────────────────────────────────────────────
    @classmethod
    def from_trials(
        cls,
        trials: Mapping[str, Tuple[dict, "pd.Series"]],
        *,
        objective: str,
        fixed: Mapping[str, object],
        meta: Mapping[str, object],
        trade_logs: Optional[Mapping[str, TradeLog]] = None,
        search_log: Optional[SearchSpaceLog] = None,
    ) -> "TrialMatrix":
        """Assemble from `config_id -> (params, periodic_return_series)`.

        Return series are outer-joined on their union period index; a period a
        config did not trade in becomes 0 R (flat that period), which is the
        honest treatment for a periodic-return matrix. Every series must carry a
        DatetimeIndex.
        """
        if not trials:
            raise ValueError("no trials to assemble")
        configs = {cid: dict(params) for cid, (params, _) in trials.items()}
        cols = {}
        for cid, (_, series) in trials.items():
            s = pd.Series(series)
            if not isinstance(s.index, pd.DatetimeIndex):
                raise ValueError(f"trial '{cid}' return series needs a DatetimeIndex")
            cols[cid] = s
        returns = pd.DataFrame(cols).sort_index().fillna(0.0)
        return cls(returns=returns, configs=configs, objective=objective,
                   fixed=dict(fixed), trade_logs=trade_logs, search_log=search_log,
                   meta=dict(meta))

    # ── counts (mind the difference) ─────────────────────────────────────────
    @property
    def n_trials(self) -> int:
        """Configs that produced a return column. NOT the correction denominator
        (see `honest_n`). n_trials <= honest_n."""
        return self.returns.shape[1]

    @property
    def honest_n(self) -> int:
        """The multiple-testing denominator: every variant TRIED, from the ledger.

        Refuses to guess. Substituting `n_trials` here omits variants that were
        tried but failed to score (errored, too few trades), undercounting the
        search — the exact bias the SearchSpaceLog exists to prevent.
        """
        if self.search_log is None:
            raise ValueError(
                "no search_log attached, so honest_n is unknowable. Attach the "
                "SearchSpaceLog the sweep recorded into; do NOT substitute n_trials.")
        return self.search_log.session_n_variants

    # ── crucible handoff helpers ─────────────────────────────────────────────
    def trial_sharpes(self) -> pd.Series:
        """Per-config Sharpe (mean/std, ddof=1) — the spread `deflated_sharpe`
        needs as its `trial_sharpes` argument. Matches crucible's own column-Sharpe
        convention: a column with zero/undefined dispersion scores 0.0 (no
        information), not NaN, so N stays consistent with `pbo_cscv`.
        """
        def _sharpe(col: pd.Series) -> float:
            r = col.to_numpy(dtype=float)
            r = r[np.isfinite(r)]
            if r.size < 2:
                return 0.0
            sd = r.std(ddof=1)
            return float(r.mean() / sd) if sd > 0 else 0.0
        return self.returns.apply(_sharpe)

    def returns_for(self, config_id: str) -> np.ndarray:
        """A config's periodic return series as an array — the `returns` argument
        for `deflated_sharpe` once you've selected the winner."""
        return self.returns[config_id].to_numpy(dtype=float)

    def log_for(self, config_id: str) -> Optional[TradeLog]:
        """The stored TradeLog for a config, if kept (e.g. the winner for
        `reality_check`). None if not retained."""
        return (self.trade_logs or {}).get(config_id)

    def __repr__(self) -> str:
        hn = self.search_log.session_n_variants if self.search_log is not None else "?"
        return (f"TrialMatrix(n_trials={self.n_trials}, honest_n={hn}, "
                f"periods={self.returns.shape[0]}, objective={self.objective!r})")
