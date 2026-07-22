"""gate — the honesty gate: the one place a verdict is allowed to become a deployment.

Every honesty investment in the layers below (the search ledger's denominator, PBO,
deflated Sharpe, the reality check) is spent at this one inch of code. If the loop can
promote a parameter set that `select` marked NOT trustworthy, all of it is decoration.
So the gate has exactly one job and no escape hatches:

  * it READS `Selection.trustworthy`; it never re-derives or re-litigates the verdict
    (the same discipline as the capital sim, which sizes downstream of the verdict and
    never re-argues it);
  * it FAILS CLOSED — anything it cannot positively confirm as trustworthy is refused;
  * it has NO bypass parameter, by design. There is no `force=`, and adding one would
    defeat the module.

**Cold start is the subtle case.** ADR-0003 says a rejected re-opt "holds the incumbent."
But on the very first cycle there is no incumbent to hold — nothing is live. Holding
nothing is not a safe no-op, it is an unhandled state, so the gate returns `halt`
instead: stay flat and alert. `hold` means "keep trading the parameters already live";
`halt` means "there is nothing safe to trade."

`is_promotable` is deliberately a pure predicate over a verdict — no currency, no clock,
no ledger. That keeps it on the promotable side of ADR-0003's promotion path, so it can
migrate into crucible later if it earns its way. `evaluate` takes `has_incumbent` as a
plain bool for the same reason: the gate must never import Seam 4.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

try:                                  # py3.8+ has Literal in typing
    from typing import Literal
    Action = Literal["promote", "hold", "halt"]
except ImportError:                   # pragma: no cover - defensive, npf targets 3.11
    Action = str                      # type: ignore[misc,assignment]

from crucible_stack.optimize import Selection


@dataclass(frozen=True)
class GateDecision:
    """What the gate decided, and the full evidence for it.

    `reasons` carries `Selection.reasons` verbatim beneath the gate's own line, so a
    reader of the ledger never has to re-run the optimizer to learn why a parameter set
    went live — or why one didn't. This is the record Seam 4 persists.
    """
    action: Action                    # "promote" | "hold" | "halt"
    reasons: Tuple[str, ...]

    @property
    def deploys(self) -> bool:
        """True only for a promotion. The one boolean the live path may act on."""
        return self.action == "promote"

    def __repr__(self) -> str:
        return f"GateDecision({self.action.upper()}, {len(self.reasons)} reasons)"


def is_promotable(selection: Optional[Selection]) -> bool:
    """May this `Selection` be deployed at all? Pure predicate over the verdict.

    Fails closed on every degenerate input: `None` (the search produced nothing) and any
    `trustworthy` that is not literally `True`. The strict identity check is deliberate —
    a truthy non-bool (a Mock in a test, a stray non-empty string, a partially built
    stub) must never read as authorization. `select` always sets a real `bool`, so the
    strictness costs nothing on the honest path.
    """
    if selection is None:
        return False
    return getattr(selection, "trustworthy", None) is True


def evaluate(selection: Optional[Selection], *, has_incumbent: bool) -> GateDecision:
    """Decide what the loop may do with this `Selection`. The gate itself.

    Returns `promote` only when the verdict is trustworthy. Otherwise the incumbent is
    held if one exists, and the loop halts if none does (see the cold-start note above).

    There is intentionally no way to override this. A caller that wants a different
    answer must produce a different `Selection`.
    """
    detail = tuple(getattr(selection, "reasons", ()) or ())

    if is_promotable(selection):
        return GateDecision(
            action="promote",
            reasons=("gate: PROMOTE - selection is trustworthy",) + detail,
        )

    if selection is None:
        cause = "gate: REFUSED - no selection was produced"
    else:
        cause = "gate: REFUSED - selection is NOT trustworthy"

    if has_incumbent:
        return GateDecision(
            action="hold",
            reasons=(cause, "gate: HOLD - keeping the incumbent parameters live") + detail,
        )

    return GateDecision(
        action="halt",
        reasons=(cause, "gate: HALT - no incumbent to fall back on; staying flat") + detail,
    )
