"""trigger — when should the loop re-optimize?

ADR-0003 commitment 2. The cadence is a *policy*, not a hardcoded rule, and this module is
the seam that keeps it one. Two honest triggers compose:

  * **schedule** — Pardo's baseline: re-optimize every N periods as the out-of-sample
    window rolls forward.
  * **drift** — the early interrupt: re-optimize as soon as the live book leaves the
    envelope it was provisioned with (`crucible_stack.orchestrate.drift`).

`any_of(schedule, drift)` is the recommended hybrid: a scheduled floor with drift able to
pull the re-optimization forward.

**This is also the substrate seam.** Nothing here knows what invokes it — cron today, a
workflow engine later (ADR-0003, Option A vs B). A trigger reads a `TriggerContext` and
returns a decision; it never learns about clocks, queues, or DAGs. That is what makes the
substrate swap a one-module change rather than a rewrite.

**Note the asymmetry with the gate, which is deliberate.** The gate fails *closed*: what it
cannot confirm as trustworthy, it refuses to deploy. A trigger fails *open*: what it cannot
assess, it re-checks. Both are the safe direction for their own job — firing a trigger only
runs the optimizer, and the gate still stands between that result and a live book, so the
cost of a spurious re-optimization is bounded. The cost is not zero, though: every
re-optimization is variants added to the `SearchSpaceLog` and therefore a larger
multiple-testing denominator, so a trigger that fires constantly quietly taxes the very
verdict it exists to refresh. Blind-but-firing is loud; blind-but-quiet is not.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, Sequence, Tuple, runtime_checkable

import numpy as np

from crucible_stack.orchestrate.drift import DriftEnvelope, check_drift

__all__ = ["TriggerContext", "TriggerDecision", "Trigger", "ScheduleTrigger",
           "DriftTrigger", "any_of"]


@dataclass(frozen=True)
class TriggerContext:
    """Everything a trigger is allowed to look at. Deliberately small.

    `realized_r` is the live book's periodic R *since the incumbent went live*, on the same
    grid the envelope was built at. Elapsed time is derived from it rather than tracked
    separately, for the same reason `DeploymentLedger.current()` is derived: two sources of
    truth for "how long has this been live" will disagree eventually.
    """
    realized_r: np.ndarray = field(default_factory=lambda: np.zeros(0))
    envelope: Optional[DriftEnvelope] = None
    has_incumbent: bool = True

    def __post_init__(self) -> None:
        r = np.asarray(self.realized_r, dtype=float)
        if r.ndim != 1:
            raise ValueError(f"realized_r must be 1-D, got shape {r.shape}")
        object.__setattr__(self, "realized_r", r)

    @property
    def elapsed(self) -> int:
        """Periods the incumbent has been live."""
        return int(self.realized_r.size)


@dataclass(frozen=True)
class TriggerDecision:
    """Whether to re-optimize now, and what asked for it.

    `sources` feeds `DeploymentEntry.trigger` on Seam 4 — join it for storage. It is a
    tuple rather than a single string because a scheduled re-opt and a drift breach can
    land on the same cycle, and collapsing that to one label loses the more urgent half.
    """
    fired: bool
    sources: Tuple[str, ...]
    reasons: Tuple[str, ...]

    def __repr__(self) -> str:
        return (f"TriggerDecision({'FIRE via ' + '+'.join(self.sources) if self.fired else 'wait'})")


@runtime_checkable
class Trigger(Protocol):
    """A re-optimization policy. The loop depends on this, never on an implementation."""
    name: str

    def __call__(self, ctx: TriggerContext) -> TriggerDecision: ...


def _cold_start(ctx: TriggerContext, name: str) -> Optional[TriggerDecision]:
    """Nothing live means nothing to wait for — every policy is due at cold start."""
    if not ctx.has_incumbent:
        return TriggerDecision(
            fired=True, sources=(name,),
            reasons=(f"{name}: no incumbent is live; the loop has nothing to preserve",))
    return None


@dataclass(frozen=True)
class ScheduleTrigger:
    """Fire every `cadence` periods — Pardo's rolling walk-forward cadence.

    The floor, not the whole policy: it guarantees the parameters are revisited on a known
    rhythm even when nothing looks wrong.
    """
    cadence: int
    name: str = "schedule"

    def __post_init__(self) -> None:
        if self.cadence < 1:
            raise ValueError(f"cadence must be >= 1 period, got {self.cadence}")

    def __call__(self, ctx: TriggerContext) -> TriggerDecision:
        cold = _cold_start(ctx, self.name)
        if cold is not None:
            return cold
        due = ctx.elapsed >= self.cadence
        return TriggerDecision(
            fired=due, sources=(self.name,) if due else (),
            reasons=(f"{self.name}: {ctx.elapsed} of {self.cadence} periods elapsed"
                     f"{' — due' if due else ''}",))


@dataclass(frozen=True)
class DriftTrigger:
    """Fire when the live book has left the envelope it was provisioned with.

    Reads the frozen envelope off the context; it never builds one, because building one
    here from current data is the re-baselining bug (see `crucible_stack.orchestrate.drift`).

    With no envelope attached the monitor is blind, and this fires with an explicit reason
    rather than staying quiet — see the fails-open note in the module docstring.
    """
    breach_level: Optional[float] = None
    name: str = "drift"

    def __call__(self, ctx: TriggerContext) -> TriggerDecision:
        cold = _cold_start(ctx, self.name)
        if cold is not None:
            return cold
        if ctx.envelope is None:
            return TriggerDecision(
                fired=True, sources=(self.name,),
                reasons=(f"{self.name}: no envelope attached, so drift cannot be assessed; "
                         "re-checking rather than trading unmonitored",))
        if ctx.elapsed == 0:
            return TriggerDecision(
                fired=False, sources=(),
                reasons=(f"{self.name}: no live periods yet",))

        v = check_drift(ctx.envelope, ctx.realized_r, breach_level=self.breach_level)
        return TriggerDecision(
            fired=v.drifted, sources=(self.name,) if v.drifted else (),
            reasons=tuple(f"{self.name}: {r}" for r in v.reasons))


@dataclass(frozen=True)
class any_of:
    """Hybrid: fire if any policy fires, and record every one that did.

    Every trigger is evaluated even once one has fired — a cheap choice that keeps the
    reasons complete, so the ledger records *all* the grounds for a re-optimization rather
    than whichever policy happened to be listed first.
    """
    triggers: Tuple[Trigger, ...]
    name: str = "any_of"

    def __init__(self, *triggers: Trigger) -> None:
        if not triggers:
            raise ValueError("any_of needs at least one trigger")
        object.__setattr__(self, "triggers", tuple(triggers))
        object.__setattr__(self, "name", "any_of")

    def __call__(self, ctx: TriggerContext) -> TriggerDecision:
        decisions = [t(ctx) for t in self.triggers]          # no short-circuit, by design
        sources = tuple(s for d in decisions for s in d.sources)
        reasons = tuple(r for d in decisions for r in d.reasons)
        return TriggerDecision(fired=any(d.fired for d in decisions),
                               sources=sources, reasons=reasons)
