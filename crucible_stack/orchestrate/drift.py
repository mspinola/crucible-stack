"""drift — has the live book escaped the envelope the validated edge predicted?

The loop's early-warning trigger. Rather than a hand-tuned tripwire ("re-optimize if
the last 3 months are down"), drift is defined against the *sim's own* block-bootstrap
distribution: the edge was provisioned with an envelope of plausible paths, and drift is
the realized path leaving it. That keeps the trigger on the same significance discipline
as every other gate in the stack.

Two traps this module is shaped to make structurally impossible:

**1. Re-baselining.** The envelope must be a snapshot FROZEN at promotion. If it were
recomputed from current data at comparison time, it would silently re-fit onto the
drifted reality and the trigger could never fire — a drift monitor incapable of detecting
drift, which looks entirely correct in review and passes any test not spanning a real
drift event. So `check_drift` takes a `DriftEnvelope` and has no parameter that could
rebuild one, and the envelope's arrays are made read-only on construction.

**2. Horizon mismatch.** A book three months live must be compared against what the sim
predicted *at three months*, not against its terminal distribution. Comparing early live
data to a five-year terminal band would flag every young book as drifted. The envelope is
therefore a per-elapsed-period grid, and `check_drift` indexes it by how long the book has
actually been running.

R-space and capital-free by construction (ADR-0003, *The promotion path*): everything
below the `envelope_from_r` constructor is pure numpy over R-multiples, with no currency,
clock, or ledger. That is the part designed to migrate into crucible if it earns its way;
promoting it means lifting the marked core section, nothing more.

Note R accumulates ADDITIVELY (3R + 2R = 5R of risk units). It does not compound, so this
module must not reuse `block_bootstrap_paths`, whose `cumprod(1 + r)` assumes fractional
returns. It shares that engine's block mechanics via `block_index` instead of growing a
second bootstrap.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence, Tuple

import numpy as np

# ─────────────────────────────────────────────────────────────────────────────────────
# PROMOTABLE CORE — pure numpy, R-space, capital-free. No npf imports below this line
# until the constructor section.
# ─────────────────────────────────────────────────────────────────────────────────────

DEFAULT_LEVELS: Tuple[float, ...] = (5.0, 25.0, 50.0, 75.0, 95.0)


def _running_drawdown(cum: np.ndarray, axis: int = -1) -> np.ndarray:
    """Peak-to-trough drawdown of a cumulative-R curve, in R (<= 0). Additive, not ratio."""
    return cum - np.maximum.accumulate(cum, axis=axis)


@dataclass(frozen=True)
class DriftEnvelope:
    """What the validated edge predicted, frozen at the moment of promotion.

    levels : quantile levels, ascending (e.g. 5, 25, 50, 75, 95).
    cum_r  : (n_periods, n_levels) cumulative-R quantiles BY ELAPSED PERIOD — row k is the
             band the sim predicted for a book k+1 periods old. This per-period shape is
             what makes an honest like-for-like comparison possible.
    max_dd : (n_levels,) quantiles of worst drawdown in R over the full horizon (<= 0).

    The arrays are made read-only on construction. This object is evidence, and evidence
    that can be edited after the fact is not evidence.
    """
    levels: Tuple[float, ...]
    cum_r: np.ndarray
    max_dd: np.ndarray
    meta: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        levels = tuple(float(x) for x in self.levels)
        if not levels:
            raise ValueError("DriftEnvelope needs at least one quantile level")
        if list(levels) != sorted(levels):
            raise ValueError(f"levels must be ascending, got {levels}")

        cum_r = np.array(self.cum_r, dtype=float, copy=True)
        max_dd = np.array(self.max_dd, dtype=float, copy=True)
        if cum_r.ndim != 2 or cum_r.shape[1] != len(levels):
            raise ValueError(
                f"cum_r must be (n_periods, {len(levels)}), got {cum_r.shape}")
        if max_dd.shape != (len(levels),):
            raise ValueError(
                f"max_dd must be ({len(levels)},), got {max_dd.shape}")

        cum_r.setflags(write=False)      # the snapshot cannot drift after promotion
        max_dd.setflags(write=False)
        object.__setattr__(self, "levels", levels)
        object.__setattr__(self, "cum_r", cum_r)
        object.__setattr__(self, "max_dd", max_dd)

    @property
    def n_periods(self) -> int:
        """The horizon this envelope was provisioned over — the comparison ceiling."""
        return int(self.cum_r.shape[0])

    def _level_index(self, level: float) -> int:
        for i, lv in enumerate(self.levels):
            if lv == level:
                return i
        raise ValueError(f"level {level} not in envelope levels {self.levels}")

    def to_dict(self) -> dict:
        """A JSON-safe, LOSSLESS rendering — pure conversion, no I/O.

        Lossless matters more than compact here. An envelope that cannot survive a restart
        forces the loop to rebuild one from current data, which is exactly the
        re-baselining bug arriving by the back door. Persisting a summary would look
        thriftier and quietly reintroduce it.
        """
        return {"levels": list(self.levels),
                "cum_r": self.cum_r.tolist(),
                "max_dd": self.max_dd.tolist(),
                "meta": dict(self.meta)}

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> "DriftEnvelope":
        """Rebuild from `to_dict`. Round-trips exactly; see the ledger's round-trip test."""
        return cls(levels=tuple(d["levels"]), cum_r=np.asarray(d["cum_r"], dtype=float),
                   max_dd=np.asarray(d["max_dd"], dtype=float),
                   meta=dict(d.get("meta") or {}))

    def __repr__(self) -> str:
        return (f"DriftEnvelope({self.n_periods} periods, "
                f"levels={self.levels}, maxdd_p{self.levels[0]:g}={self.max_dd[0]:+.2f}R)")


@dataclass(frozen=True)
class DriftVerdict:
    """Whether the live book has left its envelope, and by how much."""
    drifted: bool
    elapsed: int                  # live periods compared
    cum_r: float                  # realized cumulative R
    cum_r_floor: float            # the band's lower edge AT THIS ELAPSED PERIOD
    max_dd: float                 # realized worst drawdown, in R
    max_dd_floor: float           # the band's worst predicted drawdown
    breaches: Tuple[str, ...]     # which checks fired: "cum_r" and/or "max_dd"
    reasons: Tuple[str, ...]

    def __repr__(self) -> str:
        state = f"DRIFTED via {'+'.join(self.breaches)}" if self.drifted else "within envelope"
        return f"DriftVerdict({state}, elapsed={self.elapsed})"


def check_drift(
    envelope: DriftEnvelope,
    realized_r: Sequence[float],
    *,
    breach_level: Optional[float] = None,
) -> DriftVerdict:
    """Compare a realized periodic-R series against a FROZEN envelope.

    `realized_r` is the live book's per-period R (same period granularity the envelope was
    built at). The comparison is made at `len(realized_r)` elapsed periods, never against
    the terminal band.

    `breach_level` selects which quantile counts as the floor; defaults to the envelope's
    lowest level. Drift fires if realized cumulative R sits below that band at the current
    elapsed period, or if realized drawdown is worse than the band's worst predicted
    drawdown.

    Deliberately has no `r`/`block`/`n_sims`/`seed` parameter: there is no way to rebuild
    the envelope here, because rebuilding it is the bug this module exists to prevent.
    """
    r = np.asarray(realized_r, dtype=float)
    if r.ndim != 1:
        raise ValueError(f"realized_r must be 1-D, got shape {r.shape}")

    level = envelope.levels[0] if breach_level is None else float(breach_level)
    li = envelope._level_index(level)
    elapsed = int(r.size)

    if elapsed == 0:
        return DriftVerdict(
            drifted=False, elapsed=0, cum_r=0.0,
            cum_r_floor=float(envelope.cum_r[0, li]), max_dd=0.0,
            max_dd_floor=float(envelope.max_dd[li]), breaches=(),
            reasons=("no live periods yet; nothing to compare",),
        )
    if elapsed > envelope.n_periods:
        raise ValueError(
            f"{elapsed} live periods exceeds the envelope's {envelope.n_periods}-period "
            "horizon; the book has outlived what it was provisioned against. Re-provision "
            "at the next promotion rather than comparing past the horizon."
        )

    cum = np.cumsum(r)
    obs_cum = float(cum[-1])
    obs_dd = float(_running_drawdown(cum).min())
    cum_floor = float(envelope.cum_r[elapsed - 1, li])   # THE elapsed-aware lookup
    dd_floor = float(envelope.max_dd[li])

    breaches, reasons = [], []

    if obs_cum < cum_floor:
        breaches.append("cum_r")
        reasons.append(f"cumulative R {obs_cum:+.2f} below the p{level:g} band "
                       f"{cum_floor:+.2f} at {elapsed} periods")
    else:
        reasons.append(f"cumulative R {obs_cum:+.2f} within the p{level:g} band "
                       f"{cum_floor:+.2f} at {elapsed} periods")

    if obs_dd < dd_floor:
        breaches.append("max_dd")
        reasons.append(f"drawdown {obs_dd:+.2f}R worse than the p{level:g} "
                       f"predicted {dd_floor:+.2f}R")
    else:
        reasons.append(f"drawdown {obs_dd:+.2f}R within the p{level:g} "
                       f"predicted {dd_floor:+.2f}R")

    return DriftVerdict(
        drifted=bool(breaches), elapsed=elapsed, cum_r=obs_cum, cum_r_floor=cum_floor,
        max_dd=obs_dd, max_dd_floor=dd_floor, breaches=tuple(breaches),
        reasons=tuple(reasons),
    )


def build_envelope(
    r_paths: np.ndarray,
    *,
    levels: Sequence[float] = DEFAULT_LEVELS,
    meta: Optional[Mapping[str, object]] = None,
) -> DriftEnvelope:
    """Freeze an envelope from simulated PERIODIC-R paths, shape (n_sims, n_periods).

    Pure: takes paths, returns the snapshot. Where the paths came from is the caller's
    business, which is what keeps this function promotable.
    """
    paths = np.asarray(r_paths, dtype=float)
    if paths.ndim != 2 or paths.size == 0:
        raise ValueError(f"r_paths must be a non-empty (n_sims, n_periods) array, "
                         f"got shape {paths.shape}")

    cum = np.cumsum(paths, axis=1)
    lv = tuple(float(x) for x in levels)
    cum_q = np.percentile(cum, lv, axis=0).T          # (n_periods, n_levels)
    dd_q = np.percentile(_running_drawdown(cum, axis=1).min(axis=1), lv)

    return DriftEnvelope(
        levels=lv, cum_r=cum_q, max_dd=dd_q,
        meta={"n_sims": int(paths.shape[0]), "n_periods": int(paths.shape[1]),
              **(dict(meta) if meta else {})},
    )


# ─────────────────────────────────────────────────────────────────────────────────────
# npf-side constructor — reuses the framework MC engine's block mechanics.
# Not part of the promotable core.
# ─────────────────────────────────────────────────────────────────────────────────────

def envelope_from_r(
    r: Sequence[float],
    *,
    block: int = 6,
    n_sims: int = 1_000,
    seed: int = 0,
    levels: Sequence[float] = DEFAULT_LEVELS,
    meta: Optional[Mapping[str, object]] = None,
) -> DriftEnvelope:
    """Provision an envelope from the validated edge's periodic-R series.

    Circular block bootstrap via `montecarlo.block_index` — the same resampler under
    `block_bootstrap_paths` and `equity_bands`, so there is one set of block mechanics in
    npf, not two. Accumulation here is additive (R), not compounded.

    Call this ONCE, at promotion, and store the result. Calling it again at comparison
    time is the re-baselining bug.
    """
    from crucible_stack.framework.montecarlo import block_index

    series = np.asarray(r, dtype=float)
    if series.ndim != 1 or series.size == 0:
        raise ValueError(f"r must be a non-empty 1-D series, got shape {series.shape}")

    rng = np.random.default_rng(seed)
    n = series.size
    paths = np.empty((n_sims, n))
    for i in range(n_sims):
        paths[i] = series[block_index(rng, n, block)]

    return build_envelope(
        paths, levels=levels,
        meta={"engine": "montecarlo.block_index", "block": int(block),
              "seed": int(seed), "accumulation": "additive_R",
              **(dict(meta) if meta else {})},
    )
