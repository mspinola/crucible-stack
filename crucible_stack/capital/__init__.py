"""crucible_stack.capital — the capital-simulation layer.

Layer 2 of the toolchain. It consumes crucible's capital-free verdict artifact — a
`TradeLog` of per-trade R-multiples — plus a sizing/capital model, and produces an
`EquityResult`: the currency equity path and its account-level stats. This is where
currency first enters the toolchain, so the 1R↔currency mapping lives here (recorded as
`meta["r_denominator"]`), never in crucible.

It is downstream of crucible's gauntlet: you only size an edge that already passed. This
package owns the `EquityResult` seam (capital sim → orchestrator); crucible never imports
it. See docs/design/seam-contracts.md (Seam 3).
"""
from crucible_stack.capital.bands import EquityBands, equity_bands
from crucible_stack.capital.equity import EquityResult, EquityStats, simulate_equity

__all__ = ["EquityResult", "EquityStats", "simulate_equity",
           "EquityBands", "equity_bands"]
