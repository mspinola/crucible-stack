"""The public API surface, pinned.

ADR-0004 action item 10. `docs/api-stability.md` is the promise; this file is the
mechanism, and the promise is worth nothing without it. A stability policy that lives only
in prose gets broken by a rename in an unrelated refactor and nobody notices until an
install downstream fails.

Two things are checked, and they fail differently on purpose:

1. **Nothing disappears.** A name that vanishes or is renamed is a breaking change for
   every consumer, so this fails loudly and the fix is a deprecation shim, not an edit to
   the snapshot.
2. **Nothing appears by accident.** A new export is not breaking, but it *is* a promise:
   once shipped, removing it becomes a breaking change. Adding to the snapshot should be a
   deliberate line in a diff, not something that drifts in.

The snapshot was generated from the code rather than typed by hand, and it is regenerated
the same way (see the docstring of `test_no_public_name_disappears`). Defining this surface
was not busywork: writing it down found that `Reoptimization` — the return type a book
adapter must construct — was not exported at all, and that a downstream package had come to
depend on `montecarlo._max_drawdown`, a name marked private by convention.
"""
import importlib

import pytest

# The pinned surface. Every module here is part of the public API; every module NOT here is
# internal, whatever its names look like.
PUBLIC_API = {
    "crucible_stack.framework": {
        "RulesStrategy", "STRATEGY_REGISTRY", "TRADE_LOG_COLUMNS", "get_strategy",
        "register_strategy",
    },
    "crucible_stack.framework.strategy": {
        "RulesStrategy", "TRADE_LOG_COLUMNS",
    },
    "crucible_stack.framework.config": {
        "AlphaValidatorConfig", "AssetConfig", "BarrierDefinitions", "DataConfig",
        "ExecutionConfig", "HoldoutConfig", "MLConfig", "MasterConfig", "MetaConfig",
        "PardoWFMConfig", "SizingConfig", "StrategyConfig", "WFCGateConfig", "load_config",
    },
    "crucible_stack.framework.montecarlo": {
        "block_bootstrap", "block_bootstrap_paths", "block_index", "max_drawdown",
        "monthly_returns", "weighted_monthly_returns",
    },
    "crucible_stack.framework.registry": {
        "STRATEGY_REGISTRY", "get_strategy", "register_strategy",
    },
    "crucible_stack.engine.simulator": {
        "simulate_rules",
    },
    "crucible_stack.engine.exits": {
        "ATR_STOP_MULT", "DEFAULT_EXIT_MODE", "EXIT_RULES", "ExitRule", "MAX_HOLD_DAYS",
        "MIN_HOLD_DAYS", "get_exit", "register_exit",
    },
    "crucible_stack.optimize": {
        "Selection", "TrialMatrix", "rules_simulator", "select", "sweep",
    },
    "crucible_stack.optimize.select": {
        "OBJECTIVES", "Selection", "select",
    },
    "crucible_stack.capital": {
        "EquityBands", "EquityResult", "EquityStats", "equity_bands", "simulate_equity",
    },
    "crucible_stack.orchestrate": {
        "ACTIONS", "CycleResult", "DeploymentEntry", "DeploymentLedger", "DriftEnvelope",
        "DriftTrigger", "DriftVerdict", "GateDecision", "Reoptimization", "ScheduleTrigger",
        "Trigger", "TriggerContext", "TriggerDecision", "any_of", "build_envelope",
        "check_account_drift", "check_drift", "envelope_from_r", "evaluate", "in_currency",
        "is_promotable", "missed_windows", "monthly_r", "provision_envelope", "run_cycle",
    },
    "crucible_stack.orchestrate.account_drift": {
        "MONTHLY", "check_account_drift", "in_currency", "monthly_r", "provision_envelope",
    },
}

# Names kept alive only for compatibility. They are importable and must keep working, but
# they are not advertised and do not appear in `__all__`.
DEPRECATED_ALIASES = {
    "crucible_stack.framework.montecarlo": {"_max_drawdown": "max_drawdown"},
}


@pytest.mark.parametrize("module", sorted(PUBLIC_API))
def test_every_pinned_module_declares_its_surface(module):
    """`__all__` is the declaration. A module without one has no public API, it has an
    accident: `from x import *` then leaks every imported symbol, and readers cannot tell
    what is safe to depend on."""
    m = importlib.import_module(module)
    assert hasattr(m, "__all__"), f"{module} must declare __all__ to be part of the public API"


@pytest.mark.parametrize("module", sorted(PUBLIC_API))
def test_no_public_name_disappears(module):
    """A removal or rename breaks every consumer.

    If this fails, the fix is almost never to edit the snapshot. Keep the old name working
    (an alias, or a shim that warns) and remove it in a later release, per
    docs/api-stability.md. Only delete from the snapshot when you have decided to accept a
    breaking change and recorded it in the changelog.

    To regenerate the snapshot after a *deliberate* change:

        python -c "import importlib; [print(n, sorted(importlib.import_module(n).__all__)) \\
                   for n in ['crucible_stack.framework', ...]]"
    """
    m = importlib.import_module(module)
    missing = PUBLIC_API[module] - set(m.__all__)
    assert not missing, (
        f"{module} no longer exports {sorted(missing)}. This breaks every consumer that "
        "imports it. Keep the name working and deprecate it rather than removing it; see "
        "docs/api-stability.md.")


@pytest.mark.parametrize("module", sorted(PUBLIC_API))
def test_nothing_becomes_public_by_accident(module):
    """A new export is a promise you have to keep. Make it on purpose."""
    m = importlib.import_module(module)
    added = set(m.__all__) - PUBLIC_API[module]
    assert not added, (
        f"{module} now exports {sorted(added)}, which is not in the pinned surface. If that "
        "is intended, add it here in the same commit: once released, removing it becomes a "
        "breaking change.")


@pytest.mark.parametrize("module", sorted(PUBLIC_API))
def test_every_advertised_name_actually_resolves(module):
    """`__all__` can name something that does not exist. That survives every import test
    and fails only on `from module import *`, which nobody runs in a test suite."""
    m = importlib.import_module(module)
    for name in m.__all__:
        assert hasattr(m, name), f"{module}.__all__ advertises {name!r}, which does not exist"


def test_deprecated_aliases_still_resolve():
    """Deprecation means it keeps working, otherwise it is just a removal with a nicer name."""
    for module, aliases in DEPRECATED_ALIASES.items():
        m = importlib.import_module(module)
        for old, new in aliases.items():
            assert hasattr(m, old), f"{module}.{old} was removed rather than deprecated"
            assert getattr(m, old) is getattr(m, new), \
                f"{module}.{old} no longer points at {new}"


def test_the_registries_are_not_part_of_the_promise():
    """Deliberate carve-out, and the one people will trip over.

    `STRATEGY_REGISTRY` and `EXIT_RULES` are public *objects* whose *contents* are not:
    they ship empty and are filled by whoever imports. Their emptiness is guaranteed by
    tests/test_boundaries.py; what is in them at runtime depends entirely on what you
    imported, and no version of this package can promise anything about that.
    """
    from crucible_stack.engine.exits import EXIT_RULES
    from crucible_stack.framework import STRATEGY_REGISTRY
    assert isinstance(STRATEGY_REGISTRY, dict) and isinstance(EXIT_RULES, dict)
