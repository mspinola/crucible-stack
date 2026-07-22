# Seam contracts (design sketch)

These are the four data contracts between toolchain layers. They are the invariant;
engines (vectorbtpro today) live *behind* them and stay swappable. Style matches
crucible's existing `TradeLog` (frozen dataclass over a pandas frame, `REQUIRED`/
`OPTIONAL` column tuples, `from_*` constructors, thin accessors).

## The picture

```
        ┌───────────────── Seam 4: DeploymentLedger.current() ─────────────────┐
        │                        live params (feedback)                        │
        ▼                                                                      │
   signal ─► optimizer ──TrialMatrix──► crucible ──TradeLog──► capital sim ──EquityResult──► orchestrator
             (owns TrialMatrix)       (owns TradeLog)        (owns EquityResult)      (owns DeploymentLedger)
```

**Seam 4 is the only edge that flows backward**, and it is what turns the pipeline into a loop
(see [ADR-0003](../adr/ADR-0003-deployment-orchestrator-as-stateful-reoptimization-loop.md)).
Seams 1–3 are handoffs between pure stages. Seam 4 is a *stateful* seam: it carries the live
parameter set back to signal generation, which is why its owner is a service rather than a
library.

**Ownership / placement.** Each *producer* owns its output contract; each consumer imports
upstream. So `TradeLog` stays in crucible (exists), `TrialMatrix` lives in the optimizer
package, `EquityResult` in the capital package, and `DeploymentLedger` in the orchestrator.
Crucially, **crucible never imports `TrialMatrix`** — it consumes the raw `.returns` DataFrame,
so the core stays pure and vbtpro-free (ADR action item #4). A shared `contracts` package is the
alternative, but four owners + one-directional imports is fewer moving parts for a solo
maintainer.

---

## Seam 1 — `TrialMatrix` (optimizer → crucible)

The output of a parameter search: for every config tried, a periodic return series, plus
the params that produced it. This is exactly what `crucible.pbo_cscv` / `deflated_sharpe`
consume to price the search.

**Status: BUILT** — `crucible_stack.optimize.TrialMatrix` (`crucible_stack/optimize/trial_matrix.py`), 13 tests
in `tests/test_trial_matrix.py` incl. a live composition check against `pbo_cscv` +
`deflated_sharpe`. Lives in this package; imports only crucible.
The block below is the as-built API, not a sketch.

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple
import numpy as np, pandas as pd
from crucible.edge import TradeLog                    # optimizer -> crucible, never reverse
from crucible.validation import SearchSpaceLog        # the honest-N ledger (now in crucible)

@dataclass(frozen=True)
class TrialMatrix:
    """Every configuration a search evaluated, as a periods × configs return matrix.

    returns    : DataFrame  index = period (DatetimeIndex), columns = config_id, cells =
                 periodic return in a CONSISTENT unit (summed R/period is crucible-native).
                 This frame *is* the CSCV/PBO input.
    configs    : config_id -> the params that VARIED to produce that column.
    objective  : name of the score the search selected on (e.g. "deflated_sharpe").
    fixed      : params held CONSTANT across the search (audit trail — see note below).
    trade_logs : optional config_id -> TradeLog, kept for the winner(s) for reality_check.
    search_log : the SearchSpaceLog the sweep recorded into — source of `honest_n`.
    """
    returns: pd.DataFrame
    configs: Mapping[str, dict]
    objective: str
    fixed: Mapping[str, object]
    trade_logs: Optional[Mapping[str, TradeLog]] = None
    search_log: Optional[SearchSpaceLog] = None
    meta: Mapping[str, object] = field(default_factory=dict)

    REQUIRED_META = ("engine", "search_space")

    # __post_init__ enforces: non-empty; returns.columns == configs keys (1:1);
    # DatetimeIndex; meta carries REQUIRED_META; trade_logs keys ⊆ configs keys.

    @classmethod
    def from_trials(cls, trials: Mapping[str, Tuple[dict, pd.Series]], *,
                    objective, fixed, meta, trade_logs=None, search_log=None):
        """Assemble from config_id -> (params, periodic-R series). Series outer-joined
        on the union calendar; a period a config didn't trade in is 0 R (flat)."""
        ...

    @property
    def n_trials(self) -> int:                # columns produced — NOT the correction N
        return self.returns.shape[1]

    @property
    def honest_n(self) -> int:                # the correction denominator, from the ledger
        if self.search_log is None:
            raise ValueError("no search_log attached; honest_n is unknowable — "
                             "do NOT substitute n_trials")
        return self.search_log.session_n_variants

    def trial_sharpes(self) -> pd.Series:     # per-config Sharpe (ddof=1, 0.0 for flat) →
        ...                                   #   deflated_sharpe's `trial_sharpes` arg
    def returns_for(self, config_id) -> np.ndarray:   # winner's series → `returns` arg
        return self.returns[config_id].to_numpy(dtype=float)
    def log_for(self, config_id) -> Optional[TradeLog]:
        return (self.trade_logs or {}).get(config_id)

    # crucible handoff (correct signatures):
    #   pbo_cscv(tm.returns)                                        # .returns IS the matrix
    #   winner = tm.trial_sharpes().idxmax()
    #   deflated_sharpe(tm.trial_sharpes().to_numpy(),              # per-config Sharpes, NOT a count
    #                   returns=tm.returns_for(winner))
    #   reality_check(tm.log_for(winner))
```

**Record what was held FIXED, not just what varied.** Learned from crucible's existing
`walk_forward`: its search is duck-typed kwargs pass-through (`Strategy = Callable[..., pd.Series]`,
`strategy(prices, **combo)`) with **no signature introspection at all**. So a `param_grid` of
`{"fast": [10, 20]}` against `ma_cross(df, fast=20, slow=50, price="Close", kind="sma")`
silently leaves `slow`/`price`/`kind` at their defaults, and `WalkForwardResult` records only
the searched grid — **the audit trail is one-sided.** "What did this search actually explore?"
is unanswerable from the artifact. `TrialMatrix.fixed` closes that: the effective values of
every param that did *not* vary (including the strategy's own defaults and the barrier
`tp`/`sl`/`timeout`). Without it the seam inherits the same blind spot.

**Design decisions surfaced:**
- **Unit + granularity of `returns` is load-bearing.** It must be a *periodic* series
  (e.g. monthly summed R), consistent across columns — that's what makes CSCV and
  block-bootstrap honest. Per-trade R aggregated to periods; not raw per-trade rows.
- **`config_id`** is any stable hashable key; the `configs` map is the source of truth for
  what it means. Keep it human-readable if you can (helps audit).
- Storing `trade_logs` for *all* trials is optional and usually wasteful — keep it for the
  selected winner(s) so `reality_check` / the tearsheet can run on them.
- **`honest_n` refuses to guess.** The as-built API does not expose a fallback: `honest_n`
  raises unless a `SearchSpaceLog` is attached, so the correction denominator can only ever
  be the ledger's `session_n_variants`, never the `n_trials` column count. The anti-conflation
  rule below is enforced by the type, not left to caller discipline.

---

### Seam 1b — `SearchSpaceLog` (the search ledger)

**Don't invent search accounting; crucible already has it.** `crucible.validation.SearchSpaceLog`
(promoted out of the strategy repo per crucible's ADR-0002) is an append-only JSONL ledger of every variant tried, with
`record(params, score, status)` where status ∈ `('tried','discarded','selected')`,
`mark_selected()`, and two counts — `n_variants` (all, incl. prior runs loaded from disk) and
`session_n_variants` (this process only, so re-running a symbol can't inflate its own penalty).

Its rationale is exactly the optimizer sibling's thesis, in its own words:

> *"Aronson's data-mining-bias correction is only valid if it sees the full set of variants
> attempted — including the ones discarded — not just the winner... An incomplete log silently
> degrades Stage 3 back into the point-threshold gating the framework exists to prevent."*

And it was **designed for this job already** — its own docstring gives `'GC:parameter_search'`
as the canonical scope. A walk-forward driver that logs only the IS-window matrix, without
recording each grid point, leaves `n_variants` counting windows rather than the search.

**⚠️ `n_variants` ≠ `TrialMatrix.n_trials` — do not conflate them.** `n_trials` is
`returns.shape[1]`: configs that successfully *produced a return column*. `n_variants` counts
everything **tried, including failures** — configs that errored, or yielded too few trades to
score. So `n_variants >= n_trials`, and **the honest correction denominator is `n_variants`**.
Feeding `deflated_sharpe`/`sidak` the `TrialMatrix` column count would silently *undercount the
search* and flatter the result — the exact failure the ledger exists to prevent. **As built,
`TrialMatrix.honest_n` enforces this**: it returns `search_log.session_n_variants` and raises if
no ledger is attached, so there is no code path that hands a correction the column count.

**RESOLVED — it lives in crucible.** `SearchSpaceLog` is pure, capital-free
statistical-honesty accounting, which is crucible's remit. It was promoted there (crucible's
ADR-0002), and as of **crucible 0.3.0** the corrections take the ledger directly rather than a
count someone retyped: `sidak_correction(p, log)`, `run_gauntlet(..., n_variants=log)` and
`deflated_sharpe(..., n_trials=log)`. Before 0.3.0 `deflated_sharpe` derived N from the number
of configs it was handed, so the ledger existed for a whole release without ever reaching the
correction it was built for. Prefer passing the ledger everywhere; an int is the fallback.

---

## Seam 2 — `TradeLog` (crucible, exists)

The **pivot** both new seams orient around. Capital-free, R-multiple. Already defined in
`crucible/src/crucible/edge/trade_log.py`; unchanged. Recap of the contract:

```python
# REQUIRED = ("r",)
# OPTIONAL = ("mfe", "mae", "bars_held", "prob", "entry_date", "exit_date")
# TradeLog.from_frame(df, mapping={"pct_return": "r"})  /  TradeLog.from_arrays(r=...)
```

Everything upstream (the sweep) must be able to *emit* a `TradeLog` per config; everything
downstream (the capital sim) *consumes* one. It never carries capital, currency, or sizing —
that's the whole reason it composes.

---

## Seam 3 — `EquityResult` (capital sim → orchestrator / reporting)

The currency-world twin of `TradeLog`: what you get once a risk/sizing model turns an edge
into an account. This is the RealTest-like output crucible deliberately refuses to compute.

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Mapping
import pandas as pd

@dataclass(frozen=True)
class EquityStats:
    cagr: float
    max_dd: float          # fraction, e.g. -0.23
    sharpe: float
    sortino: float
    exposure: float        # avg fraction of capital deployed
    # extend freely — this is the layer crucible won't touch

@dataclass(frozen=True)
class EquityResult:
    """An account simulated from one or more TradeLogs under a sizing/capital model.

    equity  : Series  index = time, value = account equity (currency).
    returns : Series  periodic account returns (feeds MC-on-equity, downstream stats).
    trades  : DataFrame  per-trade records IN CURRENCY (entry/exit/price/qty/pnl/pct) —
              the currency twin of TradeLog.
    stats   : headline equity statistics (CAGR/MaxDD/Sharpe/...).
    """
    equity: pd.Series
    returns: pd.Series
    trades: pd.DataFrame
    stats: EquityStats
    meta: Mapping[str, object] = field(default_factory=dict)

    # meta MUST record the assumptions the numbers ride on:
    REQUIRED_META = ("starting_capital", "currency", "sizing_model",
                     "commission", "slippage", "r_denominator")

    def __post_init__(self) -> None:
        missing = [k for k in self.REQUIRED_META if k not in self.meta]
        if missing:
            raise ValueError(f"EquityResult.meta missing {missing}; "
                             "these assumptions are not optional.")
```

**The 1R-denominator lives HERE** (ADR action item #3). The R↔currency conversion — sizing
an R-multiple edge into currency, or its inverse when importing an external currency trade
log back into a `TradeLog` — is owned at *this* seam's adapter, recorded in
`meta["r_denominator"]`, and nowhere else. That keeps the modeling assumption in exactly
one auditable place instead of smeared across layers.

---

## Seam 4 — `DeploymentLedger` (orchestrator → live trading, and back to signal)

The append-only record of **every decision the re-optimization loop makes** — what it deployed,
what it refused to deploy, and the evidence that justified either. It is the orchestrator's
durable state, and `current()` is the live parameter set the trading path reads.

**Status: BUILT** — `crucible_stack.orchestrate.ledger` (`crucible_stack/orchestrate/ledger.py`), 24 tests in
`tests/test_ledger.py`. Specified by
[ADR-0003](../adr/ADR-0003-deployment-orchestrator-as-stateful-reoptimization-loop.md)
(commitment 1). Deliberately mirrors `SearchSpaceLog`'s idiom (append-only, in-memory by
default, opt-in JSONL) rather than `TradeLog`'s frozen-frame idiom, because this seam *is*
state — that is its whole job. The block below is the as-built API.

**Multi-book from the start.** Entries carry a `book` key and `current(book)` resolves per
book; single-book use is one key. The orchestrator is expected to run a family of strategy
books (ADR-0003 item 7), and adding a book key to an already-persisted JSONL file is a
migration where adding it up front is free.

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Mapping, Optional, Sequence, Tuple
import pandas as pd

Action = Literal["promote", "hold", "halt"]

@dataclass(frozen=True)
class DeploymentEntry:
    """One decision by the loop: what it did, why it was allowed to, on what evidence.

    Self-contained by design — an auditor must never have to re-run the optimizer to
    learn why a parameter set went live (or why one didn't).
    """
    book: str                       # multi-book from the start; one key for a single book
    timestamp: datetime
    action: Action                  # "hold"/"halt" are recorded, not just "promote"
    trigger: str                    # what fired this cycle: "schedule" | "drift"
    params: Mapping[str, object]    # the param set this decision concerns
    verdict: str                    # Selection's verdict, verbatim
    trustworthy: bool               # the gate's boolean — what actually authorized action
    reasons: Tuple[str, ...]        # Selection.reasons, verbatim (pbo / dSR / reality)
    honest_n: int                   # search cost that priced the verdict (ledger-derived)
    fit_window: Tuple[pd.Timestamp, pd.Timestamp]   # IS window the params were fit on
    envelope: Mapping[str, object]  # MC envelope SNAPSHOT at deploy time — see below
    equity_ref: Optional[str] = None   # pointer to the EquityResult run, not a copy of it


class DeploymentLedger:
    """Append-only history of loop decisions. The live param set is DERIVED, never stored.

    path=None keeps it fully in-memory (SearchSpaceLog convention); persistence is opt-in
    and writes JSONL atomically.
    """
    def __init__(self, path: Optional[str] = None) -> None: ...

    def record(self, entry: DeploymentEntry) -> None:
        """Append. Never mutates or removes prior entries."""

    def current(self) -> Optional[DeploymentEntry]:
        """The live parameter set = the most recent action == "promote".
        Returns None before the first promotion (cold start — the loop has nothing live)."""

    def history(self, action: Optional[Action] = None) -> Sequence[DeploymentEntry]: ...

    @property
    def incumbent_age(self) -> Optional[pd.Timedelta]:
        """How long the current params have been live — the schedule trigger reads this."""
```

**Design decisions surfaced:**

- **`current()` is derived, never stored.** There is no mutable "live params" field alongside the
  history. A stored current value can silently diverge from the record that justifies it, and
  then the audit trail is decorative. One source of truth: the history *is* the state.
- **Refusals are recorded, not just deployments.** `action` includes `hold` and `halt` precisely
  so the question *"why are we still on the old parameters?"* is answerable. A loop that only
  logs promotions makes its most important behaviour — declining to deploy an untrustworthy
  re-opt — invisible. ADR-0003's gate writes to the ledger on **both** branches.
- **⚠️ `envelope` is a SNAPSHOT taken at deploy time and never recomputed.** This is the subtle
  trap in the whole design. The drift monitor asks "has the live curve escaped the envelope these
  params were *provisioned with*." If the envelope is instead recomputed from current data at
  comparison time, it silently **re-baselines onto the drifted reality** and the trigger can never
  fire — a drift monitor that is structurally incapable of detecting drift. Freeze it at
  promotion and compare against the frozen copy forever.
- **⚠️ …and the snapshot must persist LOSSLESSLY, which overrides "store a compact summary."**
  Building it surfaced the corollary: if the frozen envelope cannot round-trip through the JSONL
  file, then every process restart forces the loop to rebuild one — re-baselining arriving by the
  back door, with nothing visibly wrong. So the full per-elapsed-period quantile grid is stored,
  not headline figures. A test asserts a restarted ledger yields a byte-identical drift verdict;
  rounding `cum_r` to one decimal fails it.
- **Store a reference to the `EquityResult`, not a copy.** `equity_ref` points at the run; the
  capital assumptions (starting capital, sizing model, commission, and especially the
  **1R denominator**) already live in `EquityResult.meta.REQUIRED_META` and must not be
  duplicated here. Seam 3 owns them; Seam 4 cites them.
- **`trustworthy` is stored as its own boolean**, not re-derived from `verdict` text. The gate's
  authorization is the single most safety-critical fact in the record, so it is a typed field, not
  something a future reader parses out of a string.
- **Append-only + atomic writes.** Heed the ADR-0002 lesson: a state file that *looks* isolated
  can be effectively global. Partial writes on a durable, live-trading-facing record are a much
  worse failure than a missed cycle.

**This seam is never promotable into crucible.** Durable state, a clock, and live side effects put
it on the wrong side of *both* axes in ADR-0003's promotion path. The pure primitives that read it
(a drift core in R-space, an `is_promotable` predicate) may migrate; the ledger itself never does.

---

## Why this shape

- **crucible stays the still point.** It imports none of these three new types and gains no
  capital concepts. `TrialMatrix.returns` and a `TradeLog` are all it ever sees.
- **The engine is invisible at the seams.** vbtpro produces a `TrialMatrix` and an
  `EquityResult`; nothing downstream knows it did. Swap the engine → rewrite one adapter,
  not the toolchain.
- **Assumptions are cornered.** Return unit/granularity is pinned in `TrialMatrix`; every
  capital assumption (esp. the 1R denominator) is pinned in `EquityResult.meta`. No layer
  silently inherits an unstated choice.
- **State is confined to exactly one seam.** Seams 1–3 are pure handoffs and stay trivially
  testable. `DeploymentLedger` is the *only* contract that persists, carries a clock, or feeds
  backward — so the hard parts of statefulness (durability, atomicity, cold start, audit) are
  cornered in one place instead of smeared across the toolchain, exactly as the 1R denominator is
  cornered in Seam 3.
