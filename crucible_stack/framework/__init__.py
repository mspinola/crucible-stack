"""crucible_stack.framework — the strategy-authoring interfaces.

Everything here is about how you *define* a strategy, never about any particular
one: the `RulesStrategy` ABC, the naming registry, and the config/params schema.
It is the staging area for the public `crucible-stack` package (ADR-0004), so the
rule is simple — **nothing in here may import a strategy, a data store, or npf's
application layer.** `tests/test_framework_boundary.py` enforces it.
"""
from crucible_stack.framework.registry import STRATEGY_REGISTRY, get_strategy, register_strategy
from crucible_stack.framework.strategy import TRADE_LOG_COLUMNS, RulesStrategy

__all__ = ["RulesStrategy", "TRADE_LOG_COLUMNS",
           "STRATEGY_REGISTRY", "get_strategy", "register_strategy"]
