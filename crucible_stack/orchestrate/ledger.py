"""ledger — Seam 4: the append-only record of every decision the loop makes.

The orchestrator's durable state, and the one contract in the toolchain that persists,
carries a clock, and flows *backward* (its output is the live parameter set that signal
generation reads). See docs/design/seam-contracts.md, Seam 4.

Shape, and why:

**Multi-book from the start.** Entries are keyed by `book`, and `current(book)` resolves
per book. Single-book use is simply one key. The orchestrator is expected to run a family
of strategy books, and adding a book key to an already-persisted JSONL file is a migration
where adding it now is free (ADR-0003 item 7).

**`current()` is derived, never stored.** The live parameter set is "the most recent
`promote` for this book", computed from the history. A stored current-value field can drift
from the record that justifies it, and at that point the audit trail is decorative rather
than authoritative. One source of truth: the history *is* the state.

**Refusals are recorded, not just deployments.** `hold` and `halt` get entries too, so
*"why are we still on the old parameters?"* is answerable. A ledger that logs only
promotions makes the loop's most important behaviour — declining to deploy an untrustworthy
re-optimization — invisible.

**No hidden clock.** Nothing here calls `datetime.now()`. Callers supply an entry's
timestamp, and `incumbent_age` takes `now` explicitly. A module that reads the wall clock
on its own is untestable and, worse, will happily produce a different answer for the same
inputs — unacceptable in the component whose job is to be the audit trail.

**Append-only, and it means it.** There is no update or delete. `record` appends; nothing
rewrites history.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from crucible_stack.orchestrate.drift import DriftEnvelope

try:
    from typing import Literal
    Action = Literal["promote", "hold", "halt"]
except ImportError:                   # pragma: no cover
    Action = str                      # type: ignore[misc,assignment]

__all__ = ["DeploymentEntry", "DeploymentLedger", "ACTIONS"]

ACTIONS: Tuple[str, ...] = ("promote", "hold", "halt")


def _jsonable(value: Any) -> Any:
    """Coerce numpy/pandas scalars so a params dict survives a JSON round trip."""
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


@dataclass(frozen=True)
class DeploymentEntry:
    """One decision by the loop: what it did, why it was allowed to, on what evidence.

    Self-contained on purpose — an auditor must never have to re-run the optimizer to
    learn why a parameter set went live, or why one did not.
    """
    book: str
    timestamp: datetime
    action: Action
    trigger: str                            # "schedule" | "drift" | "schedule+drift" | ...
    params: Mapping[str, Any]
    verdict: str
    trustworthy: bool                       # the gate's authorization, stored as its own
    reasons: Tuple[str, ...] = ()           #   field rather than parsed back out of text
    honest_n: int = 0
    fit_window: Optional[Tuple[pd.Timestamp, pd.Timestamp]] = None
    envelope: Optional[DriftEnvelope] = None
    equity_ref: Optional[str] = None        # points at the EquityResult; never a copy of it

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"action must be one of {ACTIONS}, got {self.action!r}")
        if not self.book:
            raise ValueError("every entry needs a book key (use one key for a single book)")
        if self.action == "promote" and not self.trustworthy:
            raise ValueError(
                "refusing to record a promotion of an untrustworthy selection; the gate "
                "is the only thing standing between a verdict and a live book, and a "
                "ledger that can record a promotion it never authorized is not evidence")
        object.__setattr__(self, "reasons", tuple(self.reasons))

    # ---- serialization (pure; the ledger owns the I/O) ----

    def to_json(self) -> str:
        fw = self.fit_window
        return json.dumps({
            "book": self.book,
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "trigger": self.trigger,
            "params": _jsonable(dict(self.params)),
            "verdict": self.verdict,
            "trustworthy": bool(self.trustworthy),
            "reasons": list(self.reasons),
            "honest_n": int(self.honest_n),
            "fit_window": [pd.Timestamp(fw[0]).isoformat(),
                           pd.Timestamp(fw[1]).isoformat()] if fw else None,
            "envelope": self.envelope.to_dict() if self.envelope is not None else None,
            "equity_ref": self.equity_ref,
        }, sort_keys=True)

    @classmethod
    def from_json(cls, line: str) -> "DeploymentEntry":
        d = json.loads(line)
        fw = d.get("fit_window")
        env = d.get("envelope")
        return cls(
            book=d["book"], timestamp=datetime.fromisoformat(d["timestamp"]),
            action=d["action"], trigger=d["trigger"], params=d["params"],
            verdict=d["verdict"], trustworthy=bool(d["trustworthy"]),
            reasons=tuple(d.get("reasons") or ()), honest_n=int(d.get("honest_n", 0)),
            fit_window=(pd.Timestamp(fw[0]), pd.Timestamp(fw[1])) if fw else None,
            envelope=DriftEnvelope.from_dict(env) if env else None,
            equity_ref=d.get("equity_ref"),
        )

    def __repr__(self) -> str:
        return (f"DeploymentEntry({self.book!r} {self.action.upper()} "
                f"@{self.timestamp:%Y-%m-%d} via {self.trigger})")


class DeploymentLedger:
    """Append-only history of loop decisions, across one or many books.

    `path=None` keeps it fully in memory (the `SearchSpaceLog` convention); persistence is
    opt-in and writes one JSON line per entry.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path
        self._entries: List[DeploymentEntry] = []
        if path and os.path.exists(path):
            self._entries = list(self._read(path))

    # ---- reading ----

    @staticmethod
    def _read(path: str) -> Iterator[DeploymentEntry]:
        with open(path, "r", encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield DeploymentEntry.from_json(line)
                except Exception as exc:                        # noqa: BLE001
                    raise ValueError(
                        f"{path}:{i} is not a readable ledger entry ({exc}). The ledger is "
                        "the audit trail; it is not repaired silently.") from exc

    @property
    def entries(self) -> Tuple[DeploymentEntry, ...]:
        return tuple(self._entries)

    @property
    def books(self) -> Tuple[str, ...]:
        return tuple(sorted({e.book for e in self._entries}))

    def history(self, book: Optional[str] = None,
                action: Optional[str] = None) -> Tuple[DeploymentEntry, ...]:
        """Entries in the order they were recorded, optionally filtered."""
        out = self._entries
        if book is not None:
            out = [e for e in out if e.book == book]
        if action is not None:
            if action not in ACTIONS:
                raise ValueError(f"action must be one of {ACTIONS}, got {action!r}")
            out = [e for e in out if e.action == action]
        return tuple(out)

    def current(self, book: str) -> Optional[DeploymentEntry]:
        """The live parameter set for `book`: its most recent `promote`.

        Derived, never stored. `None` before a book's first promotion — a cold start, where
        nothing is live and the gate must not be told to hold something that does not exist.
        """
        for e in reversed(self._entries):
            if e.book == book and e.action == "promote":
                return e
        return None

    def incumbent_age(self, book: str, now: datetime) -> Optional[pd.Timedelta]:
        """How long `book`'s live parameters have been live, as of an explicit `now`.

        `now` is a parameter and not a call to the system clock, so this is deterministic
        and testable — see the no-hidden-clock note in the module docstring.
        """
        cur = self.current(book)
        return None if cur is None else pd.Timestamp(now) - pd.Timestamp(cur.timestamp)

    # ---- writing ----

    def record(self, entry: DeploymentEntry) -> DeploymentEntry:
        """Append one decision. Never mutates or removes a prior entry."""
        if not isinstance(entry, DeploymentEntry):
            raise TypeError(f"record expects a DeploymentEntry, got {type(entry).__name__}")
        self._entries.append(entry)
        if self.path:
            self._append_line(self.path, entry.to_json())
        return entry

    @staticmethod
    def _append_line(path: str, payload: str) -> None:
        """Append one complete line, flushed and fsynced before returning.

        A single write of one newline-terminated line under O_APPEND is what keeps a
        half-written record from becoming the tail of this file. Deliberately not a
        read-modify-write of the whole file: rewriting an append-only audit log to add a
        line is both slower and a much larger blast radius if it fails midway.
        """
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(payload + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        where = self.path or "in-memory"
        return f"DeploymentLedger({len(self._entries)} entries, {len(self.books)} books, {where})"
