"""runner — one turn of the loop: trigger → re-optimize → gate → ledger.

This is where the pieces finally compose. `run_cycle` is the whole operating system in one
function, and it is deliberately boring: every interesting decision was already made and
tested in `trigger`, `gate`, and `ledger`, so this reads as plumbing rather than policy.
That is the intended shape — a loop with judgement scattered through it is a loop nobody
can audit.

**Re-optimization is injected, not imported.** The cycle takes a `reoptimize` callable
returning a `Reoptimization`. The orchestrator must never import strategy code (it would
break the extraction boundary pinned in `tests/test_orchestrate_boundaries.py`), and it
genuinely does not need to: what it needs is a `Selection` and its provenance, not
knowledge of how the book was swept.

**The clock enters here and nowhere below.** `run_cycle` takes `now` explicitly and the
entrypoint (`python -m crucible_stack.orchestrate`) is the single place that reads the system clock.
Everything underneath stays deterministic and testable.

**A refusal never carries an envelope.** On `hold`/`halt` the entry records the *candidate*
params — that is the audit value, "here is what we tried and would not deploy" — but no
envelope, because an envelope is the authorization artifact of a promotion. A rejected
candidate provisioned nothing. This also closes the last path by which a rejected envelope
could be picked up and used as a baseline: `current()` returns only promotions, and refusals
carry nothing to re-baseline onto.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from crucible_stack.optimize import Selection
from crucible_stack.orchestrate.drift import DriftEnvelope
from crucible_stack.orchestrate.gate import GateDecision, evaluate
from crucible_stack.orchestrate.ledger import DeploymentEntry, DeploymentLedger
from crucible_stack.orchestrate.trigger import Trigger, TriggerContext, TriggerDecision

__all__ = ["Reoptimization", "CycleResult", "run_cycle", "missed_windows"]


@dataclass(frozen=True)
class Reoptimization:
    """What a re-optimization hands back: the verdict plus the provenance to record.

    `envelope` is what the book WOULD be provisioned with if this candidate is promoted.
    It is only ever written to the ledger on a promotion.
    """
    selection: Selection
    fit_window: Optional[Tuple[pd.Timestamp, pd.Timestamp]] = None
    envelope: Optional[DriftEnvelope] = None
    equity_ref: Optional[str] = None


@dataclass(frozen=True)
class CycleResult:
    """The outcome of one turn, whether or not anything happened."""
    book: str
    fired: bool
    trigger: TriggerDecision
    gate: Optional[GateDecision] = None
    entry: Optional[DeploymentEntry] = None
    missed: int = 0
    reasons: Tuple[str, ...] = ()

    @property
    def deployed(self) -> bool:
        return self.entry is not None and self.entry.action == "promote"

    def __repr__(self) -> str:
        what = self.entry.action.upper() if self.entry is not None else "no-op"
        return f"CycleResult({self.book!r} {what}, missed={self.missed})"


def missed_windows(elapsed: int, cadence: Optional[int]) -> int:
    """How many scheduled re-optimizations were skipped before this one.

    Cron does not tell you it failed to run; the machine was asleep, the job errored, the
    laptop was shut. Comparing elapsed periods against the cadence is how a silently
    skipped window becomes visible rather than being absorbed as if nothing happened.

    `elapsed == cadence` is on time (0 missed); twice the cadence means one was missed.
    """
    if not cadence or cadence < 1 or elapsed < cadence:
        return 0
    return max(0, elapsed // cadence - 1)


def _verdict_label(sel: Optional[Selection]) -> str:
    if sel is None:
        return "NO SELECTION"
    return "TRUSTWORTHY" if sel.trustworthy else "NOT TRUSTWORTHY"


def run_cycle(
    *,
    book: str,
    ledger: DeploymentLedger,
    trigger: Trigger,
    reoptimize: Callable[[], Reoptimization],
    realized_r: Sequence[float],
    now: datetime,
    cadence: Optional[int] = None,
) -> CycleResult:
    """Run one turn of the loop for one book.

    Reads the book's incumbent (and its frozen envelope) off the ledger, asks the trigger
    whether to act, and — only if it fires — re-optimizes, gates the result, and records
    the decision. A cycle that does not fire records nothing: a non-event is not a
    decision, and writing one per cron tick would bury the real entries.
    """
    incumbent = ledger.current(book)
    r = np.asarray(realized_r, dtype=float)

    ctx = TriggerContext(
        realized_r=r,
        envelope=incumbent.envelope if incumbent is not None else None,
        has_incumbent=incumbent is not None,
    )
    tdec = trigger(ctx)
    missed = missed_windows(ctx.elapsed, cadence)
    notes: Tuple[str, ...] = ()
    if missed:
        notes += (f"WARNING: {missed} scheduled window(s) appear to have been skipped "
                  f"({ctx.elapsed} periods elapsed on a {cadence}-period cadence)",)

    if not tdec.fired:
        return CycleResult(book=book, fired=False, trigger=tdec, missed=missed,
                           reasons=notes + tdec.reasons)

    reopt = reoptimize()
    sel = reopt.selection
    gdec = evaluate(sel, has_incumbent=incumbent is not None)
    promoting = gdec.action == "promote"

    entry = DeploymentEntry(
        book=book,
        timestamp=now,
        action=gdec.action,
        trigger="+".join(tdec.sources) or "unknown",
        params=dict(getattr(sel, "params", {}) or {}),
        verdict=_verdict_label(sel),
        trustworthy=bool(getattr(sel, "trustworthy", False)),
        reasons=notes + tdec.reasons + gdec.reasons,
        honest_n=int(getattr(sel, "honest_n", 0) or 0),
        fit_window=reopt.fit_window,
        envelope=reopt.envelope if promoting else None,   # refusals provision nothing
        equity_ref=reopt.equity_ref if promoting else None,
    )
    ledger.record(entry)

    return CycleResult(book=book, fired=True, trigger=tdec, gate=gdec, entry=entry,
                       missed=missed, reasons=entry.reasons)
