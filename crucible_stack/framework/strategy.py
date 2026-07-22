"""
strategies/base.py — the pluggable rules-strategy interface for the WFA engine.

A `RulesStrategy` exposes vectorized callbacks (`build_frame` / `setup` / `trigger`)
plus declarative execution specs (`stop_spec` / `exit_spec` / `entry_fill`). The
generic simulator (`strategies/simulator.py`) turns those into a per-trade
R-multiple trade log; `adapters/rules_adapter.py` runs it per walk-forward window
and hands the trade log to the existing Pardo scoring stack.

The abstraction is the common shape both existing harnesses already share
(`simulate_cmr_native` and `option1_filter.simulate_trigger_entries`):
    build feature frame → per-bar setup gate → per-bar entry trigger
    → stop model → exit model → fill.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import pandas as pd

# The per-trade columns the Pardo scoring stack consumes (see pure_edge/simulator.py
# and pure_edge/metrics.calculate_phase_3_metrics). `pct_return` is in R-multiples.
TRADE_LOG_COLUMNS = [
    'symbol', 'asset_class', 'entry_date', 'exit_date', 'side',
    'entry_price', 'exit_price', 'tp_price', 'sl_price', 'exit_reason',
    'pct_return', 'mfe', 'mae', 'mfe_10', 'mae_10', 'bars_held', 'prob_success',
]


class RulesStrategy(ABC):
    """Base class for a rules-based strategy. Subclasses declare the callbacks and
    specs; parameters live in `self.params` so a config (or a grid-tuner) can vary
    the behaviour without touching code."""

    name: str = "base"

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        self.params: Dict[str, Any] = dict(params or {})
        # Execution specs — subclasses may override in __init__ from self.params.
        self.entry_fill: str = self.params.get('entry_fill', 'next_open')   # 'close' | 'next_open'
        self.stop_spec: Dict[str, Any] = {'mode': 'wick'}
        # Deliberately empty: the base class expresses no opinion about how a strategy
        # exits, so the engine's own default applies (see `DEFAULT_EXIT_MODE`). This
        # used to default to 'cot_neutral', which meant a strategy that never set an
        # exit silently inherited one reading COT columns its frame may not have.
        self.exit_spec: Dict[str, Any] = {}
        self.param_grid: Dict[str, list] = {}

    def with_params(self, params: Dict[str, Any]) -> "RulesStrategy":
        """Return a fresh instance with `params` merged over the current ones
        (used by the walk-forward optimizer to sweep the grid)."""
        return self.__class__(params={**self.params, **params})

    # ── callbacks ────────────────────────────────────────────────────────────
    @abstractmethod
    def build_frame(self, symbol: str, is_equity: bool) -> Optional[pd.DataFrame]:
        """Daily OHLC + release-lagged COT columns + ATR + trigger primitives.
        Built once on full history; the adapter slices it per window."""

    def frame_key(self, symbol: str):
        """Hashable identity of this strategy's `build_frame(symbol)` output, for
        caching across configs. Default assumes the frame depends only on the
        strategy class + symbol; override to add any *params* that change the frame
        (so two configs that build different frames don't share a cache entry)."""
        return (type(self).__name__, symbol)

    @abstractmethod
    def setup(self, df: pd.DataFrame, direction: str, is_equity: bool) -> pd.Series:
        """Boolean per-bar positioning gate for `direction` ('long'/'short')."""

    @abstractmethod
    def trigger(self, df: pd.DataFrame, direction: str) -> pd.Series:
        """Boolean per-bar entry trigger for `direction`."""
