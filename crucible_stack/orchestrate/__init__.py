"""crucible_stack.orchestrate — the deployment orchestrator.

Layer 3 of the toolchain, and the only layer that is a *service* rather than a library:
it owns a clock, durable state, and side effects on a live book. It composes the layers
below it (optimizer -> crucible -> capital sim) into Pardo's walk-forward analysis
reframed as a live re-optimization loop, rather than a one-time validation gate.

It adds no new math. What it contributes is state, time, and control flow.

The piece built first is the **honesty gate** (`crucible_stack.orchestrate.gate`): the single point
where a statistical verdict is allowed to become a deployment. Everything else in this
package is scaffolding around that decision.

See docs/adr/ADR-0003-deployment-orchestrator-as-stateful-reoptimization-loop.md and
docs/design/seam-contracts.md (Seam 4).
"""
from crucible_stack.orchestrate.gate import GateDecision, evaluate, is_promotable
from crucible_stack.orchestrate.drift import (
    DriftEnvelope, DriftVerdict, build_envelope, check_drift, envelope_from_r,
)
from crucible_stack.orchestrate.account_drift import (
    check_account_drift, in_currency, monthly_r, provision_envelope,
)
from crucible_stack.orchestrate.trigger import (
    DriftTrigger, ScheduleTrigger, Trigger, TriggerContext, TriggerDecision, any_of,
)
from crucible_stack.orchestrate.ledger import ACTIONS, DeploymentEntry, DeploymentLedger
from crucible_stack.orchestrate.runner import (
    CycleResult, Reoptimization, missed_windows, run_cycle,
)

__all__ = ["GateDecision", "evaluate", "is_promotable",
           "DriftEnvelope", "DriftVerdict", "build_envelope", "check_drift",
           "envelope_from_r",
           "provision_envelope", "check_account_drift", "monthly_r", "in_currency",
           "Trigger", "TriggerContext", "TriggerDecision", "ScheduleTrigger",
           "DriftTrigger", "any_of",
           "DeploymentEntry", "DeploymentLedger", "ACTIONS",
           "run_cycle", "CycleResult", "Reoptimization", "missed_windows"]
