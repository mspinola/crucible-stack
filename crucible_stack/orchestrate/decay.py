"""decay - has the promoted edge itself weakened, rather than the path wandered?

The second half of the monitoring story. `drift` watches the equity **path**: realized
cumulative R against the block-bootstrap envelope frozen at promotion. This watches the
per-trade **parameter**: expectancy, and how often the signal fires at all.

They are complements, not duplicates, and they come apart in both directions. A book
whose expectancy halves while its trade count holds produces a path that goes flat, and
flat usually sits inside a p5 band provisioned over a multi-year horizon. Conversely a
run of correlated losers breaches the drawdown floor with every per-trade statistic
exactly where it should be. Running only one of the two leaves a real failure invisible.

The judging is crucible's (`crucible.validation.monitor`); this module contributes state,
time and control flow, the same division of labour as `drift`. Specifically it contributes
the three things crucible refuses to own:

  * **freezing** an `EdgeBaseline` at the moment of promotion, and persisting it in the
    ledger beside the `DriftEnvelope`
  * **reading** it back on a later cycle without any means of rebuilding it
  * turning a verdict into a **re-optimization trigger**

**Only DEGRADED fires.** crucible's monitor returns HOLDING / SLIPPING / DEGRADED, and
only the CUSUM (which carries a stated false-alarm rate) can produce DEGRADED. The soft
channels cap at SLIPPING and are reported here without firing. Letting SLIPPING trigger a
re-optimization would import an uncalibrated tripwire into the loop and undo the whole
reason crucible separates them: a trailing window on a book whose edge never decayed
still crosses the soft line often enough to matter.

**Per-trade R, not periodic R.** `TriggerContext.realized_r` is periodic (the grid the
drift envelope was built on). The monitor consumes per-trade R, which is a different
series with a different length, so it rides on its own field. Denominating one in the
other is the units bug crucible fixed in v0.4.0 and is not worth repeating.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping, Optional, Sequence

from crucible.edge import TradeLog
from crucible.validation import EdgeBaseline, MonitorVerdict, Thresholds, edge_monitor

# Re-exported so `trigger` depends on this adapter rather than on crucible directly,
# exactly as it depends on `drift.check_drift` rather than reimplementing the band. That
# keeps the trigger seam's import surface to crucible_stack + numpy, which
# `test_triggers_know_nothing_about_the_substrate` pins.
__all__ = ["check_decay", "baseline_to_dict", "baseline_from_dict",
           "EdgeBaseline", "MonitorVerdict", "Thresholds"]


def check_decay(baseline: EdgeBaseline, trade_r: Sequence[float], *,
                trade_dates: Optional[Sequence[Any]] = None,
                thresholds: Optional[Thresholds] = None) -> MonitorVerdict:
    """Judge a frozen baseline against per-TRADE R since promotion. The judging is
    crucible's; what this adds is the seam.

    `trade_r` is per-trade, not the periodic series `check_drift` consumes. Deliberately
    has no parameter from which a baseline could be rebuilt, matching `check_drift`.

    `trade_dates` are the live trades' entry dates, parallel to `trade_r`. They feed one
    channel only: crucible derives the live firing rate from them and compares it against
    `baseline.trades_per_year`. Passing R alone leaves that channel permanently off, which
    is not a neutral default, because it is the channel that catches a signal quietly
    ceasing to fire while per-trade expectancy still reads full size. Omitting them stays
    legal (a book that cannot date its trades is honestly reported as having the channel
    off) but that should be a fact about the book rather than an accident of the seam.
    """
    return edge_monitor(TradeLog.from_arrays(trade_r, entry_date=trade_dates), baseline,
                        thresholds=thresholds or Thresholds())


def baseline_to_dict(baseline: EdgeBaseline) -> dict:
    """JSON-safe, lossless rendering of a frozen baseline. Pure conversion, no I/O.

    `EdgeBaseline` is a plain frozen dataclass of scalars in the layer below, and crucible
    deliberately persists nothing, so the round-trip lives here alongside the ledger that
    needs it (same arrangement as `DriftEnvelope.to_dict`).
    """
    d = asdict(baseline)
    return {k: (None if v is None else v) for k, v in d.items()}


def baseline_from_dict(d: Optional[Mapping[str, Any]]) -> Optional[EdgeBaseline]:
    """Rebuild from `baseline_to_dict`. Round-trips exactly; see the ledger's test.

    Reconstruction runs `EdgeBaseline.__post_init__`, so a corrupted or hand-edited entry
    (a non-positive expectancy, say) raises here rather than silently producing a monitor
    that can never fire.
    """
    if not d:
        return None
    return EdgeBaseline(
        expectancy=float(d["expectancy"]),
        sigma=float(d["sigma"]),
        n_trades=int(d["n_trades"]),
        trades_per_year=None if d.get("trades_per_year") is None
        else float(d["trades_per_year"]),
        n_variants=None if d.get("n_variants") is None else int(d["n_variants"]),
        deflated=bool(d.get("deflated", False)),
    )
