"""exits — the rules engine's pluggable exit registry.

`simulate_rules` used to dispatch exits through an if/elif chain that mixed generic
modes (barriers, channel, atr_trail) with COT-specific ones (cot_neutral,
willco_neutral) — and defaulted to a COT mode. That made the engine unusable as a
general framework: a rules simulator whose default exit reads `comms_idx` is a COT
simulator wearing a framework's clothes (ADR-0004).

An exit rule is **per-trade state plus a per-bar test**. It is constructed at entry
and its `check` runs once per held bar, in the same place the old if/elif branch ran:
after the hard/close stop checks, before the trailing-stop ratchet. Returning
`(price, reason)` ends the trade; returning `None` continues, and a rule may update
its own state on the way past — that is how `atr_trail` ratchets.

Generic rules live here. Strategy-specific ones register themselves from the strategy
package (see `npf.strategies.cot_exits`), which is what lets the engine ship without
them.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple, Type

import numpy as np

__all__ = ["ExitRule", "register_exit", "get_exit", "EXIT_RULES",
           "MIN_HOLD_DAYS", "MAX_HOLD_DAYS", "ATR_STOP_MULT", "DEFAULT_EXIT_MODE"]

# Engine defaults. These were imported from the CMR module, which made the engine
# depend on a strategy; they are the engine's own now. `tests/test_exit_registry.py`
# pins them equal to the CMR module's copies so the two cannot drift apart silently.
MIN_HOLD_DAYS = 3       # ignore same-week neutral noise right after entry
MAX_HOLD_DAYS = 400     # safety cap (~18 months) if a ride never ends
ATR_STOP_MULT = 1.5

# `barriers` is take-profit + timeout: it needs nothing from the frame beyond OHLC, so
# it is the only honest default for an engine that does not know your strategy.
DEFAULT_EXIT_MODE = "barriers"

Exit = Optional[Tuple[float, str]]


class ExitRule:
    """One trade's exit logic. Subclass, set `name`, and register it.

    `check` is called once per held bar and returns `(exit_price, exit_reason)` to
    close the trade, or `None` to continue. State that must persist across bars (a
    ratcheting trail, a running extreme) belongs on `self`.
    """
    name: str = ""

    def __init__(self, *, side: int, entry: float, risk: float, atr_at_entry: float,
                 direction: str, is_equity: bool, spec: Mapping[str, Any]) -> None:
        self.side = side
        self.entry = entry
        self.risk = risk
        self.atr_at_entry = atr_at_entry
        self.direction = direction
        self.is_equity = is_equity
        self.spec = spec

    def check(self, bar: Mapping[str, Any], held: int) -> Exit:  # pragma: no cover
        raise NotImplementedError


EXIT_RULES: Dict[str, Type[ExitRule]] = {}


def register_exit(cls: Type[ExitRule]) -> Type[ExitRule]:
    """Register an exit rule under its `name`. Usable as a decorator."""
    if not getattr(cls, "name", ""):
        raise ValueError(f"{cls.__name__} needs a non-empty `name` to register")
    EXIT_RULES[cls.name] = cls
    return cls


def get_exit(mode: str) -> Type[ExitRule]:
    """Look up a registered exit rule, or say what is available.

    A missing mode is usually a missing *registration* — strategy-specific rules are
    registered by importing the package that defines them.
    """
    if mode not in EXIT_RULES:
        raise ValueError(
            f"unknown exit mode {mode!r}; registered: {sorted(EXIT_RULES)}. "
            "Strategy-specific exits register on import of the package that defines "
            "them (e.g. `import npf.strategies` registers the COT exits).")
    return EXIT_RULES[mode]


# ── generic rules ────────────────────────────────────────────────────────────────

@register_exit
class BarriersExit(ExitRule):
    """Intrabar take-profit at tp x R, plus a hard bar-count timeout."""
    name = "barriers"

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.tp_price = self.entry + self.side * float(self.spec.get("tp", 2.0)) * self.risk
        self.timeout = int(self.spec.get("timeout", 56))

    def check(self, bar, held) -> Exit:
        if self.side > 0 and bar["High"] >= self.tp_price:
            return self.tp_price, "tp"
        if self.side < 0 and bar["Low"] <= self.tp_price:
            return self.tp_price, "tp"
        if held >= self.timeout:
            return bar["Close"], "timeout"
        return None


@register_exit
class ChannelExit(ExitRule):
    """Donchian trend exit: close through the opposite N-day channel.

    `dc_lo_exit` / `dc_hi_exit` are precomputed in the strategy frame with a
    `.shift(1)`, so the level at bar j uses only closes strictly before j.
    """
    name = "channel"

    def check(self, bar, held) -> Exit:
        lo_ch = bar.get("dc_lo_exit", np.nan)
        hi_ch = bar.get("dc_hi_exit", np.nan)
        if self.side > 0 and not np.isnan(lo_ch) and bar["Close"] < lo_ch:
            return bar["Close"], "channel"
        if self.side < 0 and not np.isnan(hi_ch) and bar["Close"] > hi_ch:
            return bar["Close"], "channel"
        if held >= MAX_HOLD_DAYS:
            return bar["Close"], "maxhold"
        return None


@register_exit
class AtrTrailExit(ExitRule):
    """Clenow-style wide ATR trailing exit off the best close so far.

    Distinct from the `atr_trail` STOP mode: there the initial k x ATR stop defines R
    and is the catastrophic floor, while this is the giveback exit that binds once a
    trade runs. The close is tested against a trail set from bars strictly before this
    one; the ratchet then updates with this bar.
    """
    name = "atr_trail"

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.mult = float(self.spec.get("mult", 3.0))
        self.run_close_ext = self.entry
        self.trail_lvl = self.entry - self.side * self.mult * self.atr_at_entry

    def check(self, bar, held) -> Exit:
        if ((self.side > 0 and bar["Close"] < self.trail_lvl)
                or (self.side < 0 and bar["Close"] > self.trail_lvl)):
            return bar["Close"], "trail"
        if held >= MAX_HOLD_DAYS:
            return bar["Close"], "maxhold"
        batr = bar.get("ATR", 0.0)
        if batr > 0:
            self.run_close_ext = (max(self.run_close_ext, bar["Close"]) if self.side > 0
                                  else min(self.run_close_ext, bar["Close"]))
            cand = (self.run_close_ext - self.mult * batr if self.side > 0
                    else self.run_close_ext + self.mult * batr)
            self.trail_lvl = (max(self.trail_lvl, cand) if self.side > 0
                              else min(self.trail_lvl, cand))    # ratchet only
        return None


@register_exit
class RejectExit(ExitRule):
    """Exit on a daily reversal candle (move exhaustion).

    A long ends on a bearish-rejection bar, a short on a bullish one. Needs
    `bear_rejection_score` / `bull_rejection_score` on the frame — a strategy-provided
    column, but the rule itself carries no strategy semantics.
    """
    name = "reject_exit"

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.thr = float(self.spec.get("reject_thr", 1.0))

    def check(self, bar, held) -> Exit:
        rej = (bar.get("bear_rejection_score", np.nan) if self.side > 0
               else bar.get("bull_rejection_score", np.nan))
        if held >= MIN_HOLD_DAYS and (not np.isnan(rej)) and rej >= self.thr:
            return bar["Close"], "reject"
        if held >= MAX_HOLD_DAYS:
            return bar["Close"], "maxhold"
        return None
