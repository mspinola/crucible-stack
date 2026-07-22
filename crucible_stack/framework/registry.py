"""registry — how a strategy is named and looked up.

The mechanism is framework; the *contents* are not. `STRATEGY_REGISTRY` ships empty
and strategy packages register into it, exactly as the exit rules do
(`framework`-side generic exits, strategy-side COT exits). That is what lets the
framework be imported without any particular strategy — and without whatever data
store that strategy needs at import time.
"""
from __future__ import annotations

from typing import Dict, Type

from crucible_stack.framework.strategy import RulesStrategy

__all__ = ["STRATEGY_REGISTRY", "register_strategy", "get_strategy"]

STRATEGY_REGISTRY: Dict[str, Type[RulesStrategy]] = {}


def register_strategy(name: str, cls: Type[RulesStrategy]) -> Type[RulesStrategy]:
    """Register `cls` under `name` (stored lower-case; lookup is case-insensitive)."""
    if not name:
        raise ValueError("a strategy needs a non-empty name to register")
    STRATEGY_REGISTRY[name.lower()] = cls
    return cls


def get_strategy(name: str) -> type:
    """Return the RulesStrategy subclass registered under `name` (case-insensitive,
    so a config's display-cased "NPF" resolves to the 'npf' key)."""
    key = name.lower()
    if key not in STRATEGY_REGISTRY:
        raise ValueError(
            f"Unknown strategy '{name}'. Registered: {sorted(STRATEGY_REGISTRY)}"
        )
    return STRATEGY_REGISTRY[key]
