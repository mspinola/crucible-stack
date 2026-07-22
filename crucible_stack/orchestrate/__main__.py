"""The v1 cron substrate: `python -m crucible_stack.orchestrate`.

ADR-0003 Option A. Deliberately the thinnest thing that works — argument parsing, one read
of the system clock, one call to `run_cycle`. All the judgement lives below this file, and
that is what makes the substrate swappable: replacing cron with a workflow engine means
rewriting this module and nothing else.

Book wiring is resolved by dotted path (`--book-factory pkg.mod:callable`) rather than
imported, so the orchestrator stays free of strategy code and keeps its extraction boundary.
The factory takes no arguments and returns an object exposing:

    .reoptimize()                    -> Reoptimization
    .realized_r_since(since, params) -> periodic R since the incumbent went live

`since` and `params` come off the ledger's current entry, not from the book. The book cannot
know when its parameters were promoted, and a realized series measured over the wrong window
would be compared against the envelope's band for a different elapsed period — a silently
wrong answer rather than an error.

Exit codes are chosen for cron's benefit: a non-zero status is how an unattended job gets
someone's attention.

    0  cycle completed — no-op, hold, or promote
    3  HALT: nothing is safe to trade (no incumbent, and the candidate was refused)
    4  a scheduled window was skipped (the job did not run when it should have)
    1  the cycle raised

Example crontab entry (monthly, 06:30 on the 1st):

    30 6 1 * *  cd /path/to/repo && mkdir -p var && .venv/bin/python -m crucible_stack.orchestrate \\
                  --book my-book --ledger var/deployments.jsonl \\
                  --book-factory my_books.trend:build --cadence 6 --quiet >> var/loop.log 2>&1
"""
from __future__ import annotations

import argparse
import importlib
import sys
from datetime import datetime

from crucible_stack.orchestrate.ledger import DeploymentLedger
from crucible_stack.orchestrate.runner import run_cycle
from crucible_stack.orchestrate.trigger import DriftTrigger, ScheduleTrigger, any_of

EXIT_OK, EXIT_ERROR, EXIT_HALT, EXIT_MISSED = 0, 1, 3, 4


def _resolve(spec: str):
    """Import `pkg.module:attr` without the orchestrator statically depending on it."""
    if ":" not in spec:
        raise ValueError(f"--book-factory must look like 'pkg.module:callable', got {spec!r}")
    module, _, attr = spec.partition(":")
    return getattr(importlib.import_module(module), attr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m crucible_stack.orchestrate",
        description="Run one turn of the re-optimization loop for one book.")
    p.add_argument("--book", required=True, help="book key on the ledger, e.g. 'npf'")
    p.add_argument("--ledger", required=True, help="path to the append-only JSONL ledger")
    p.add_argument("--book-factory",
                   help="dotted path 'pkg.module:callable' returning the book wiring "
                        "(required to run a cycle; not needed for --status)")
    p.add_argument("--cadence", type=int, default=6,
                   help="scheduled re-optimization cadence in months (default: 6)")
    p.add_argument("--breach-level", type=float, default=None,
                   help="quantile counting as a drift breach (default: envelope's lowest)")
    p.add_argument("--dry-run", action="store_true",
                   help="evaluate and report, but write nothing to the ledger")
    p.add_argument("--status", action="store_true",
                   help="report what is live from the ledger and exit; runs no cycle, "
                        "needs no book factory and no data store")
    p.add_argument("--quiet", action="store_true",
                   help="say nothing when nothing happened. A cycle that does not fire "
                        "prints no output, so cron mails you only when there is a decision, "
                        "a skipped window, or a failure")
    return p


def _report_status(args) -> int:
    """What is live, since when, and how overdue — read straight off the ledger.

    Deliberately needs neither the book factory nor COTDATA_STORE: the question "what am I
    running right now?" should be answerable in a second, on any machine, without loading a
    data store. The overdue estimate is therefore CALENDAR months since promotion, which is
    a close cousin of the loop's own count (months in which the book actually traded) but
    not identical — it is a prompt to run a cycle, not a substitute for one.
    """
    ledger = DeploymentLedger(args.ledger)
    live = ledger.current(args.book)
    history = ledger.history(args.book)

    print(f"[orchestrate] {args.book}  ({len(history)} decision(s) on {args.ledger})")
    if live is None:
        print("  LIVE: nothing — no promotion on record, so the book is flat.")
    else:
        age = ledger.incumbent_age(args.book, datetime.now())
        months = age.days / 30.44
        print(f"  LIVE: {live.params}")
        print(f"    promoted   : {live.timestamp:%Y-%m-%d} ({age.days}d ago, ~{months:.1f} months)")
        print(f"    verdict    : {live.verdict} (honest_n={live.honest_n})")
        if live.envelope is not None:
            print(f"    envelope   : {live.envelope.n_periods} periods, frozen at promotion")
        if any("NOT the same metric" in r for r in live.reasons):
            print("    caveat     : selected and corrected on DIFFERENT metrics (see reasons)")
        overdue = int(months // args.cadence) - 1 if months >= args.cadence else 0
        if overdue > 0:
            print(f"    OVERDUE    : ~{overdue} cadence window(s) past due "
                  f"({args.cadence}-month cadence) — run a cycle")

    last = history[-1] if history else None
    if last is not None and (live is None or last is not live):
        print(f"  last decision: {last.action.upper()} on {last.timestamp:%Y-%m-%d} "
              f"via {last.trigger}")
    counts = {a: len(ledger.history(args.book, action=a)) for a in ("promote", "hold", "halt")}
    print(f"  history: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return EXIT_OK


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.status:
        try:
            return _report_status(args)
        except Exception as exc:                   # noqa: BLE001
            print(f"[orchestrate] status FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return EXIT_ERROR

    if not args.book_factory:
        print("[orchestrate] --book-factory is required to run a cycle "
              "(omit it only with --status)", file=sys.stderr)
        return EXIT_ERROR

    try:
        book = _resolve(args.book_factory)()
        # in-memory ledger for a dry run: the cycle runs identically, it just leaves no trace
        ledger = DeploymentLedger(None if args.dry_run else args.ledger)
        if args.dry_run:
            for e in DeploymentLedger(args.ledger).entries:
                ledger._entries.append(e)          # read history, refuse to extend it

        # the realized window is defined by the ledger, not the book: R since the
        # incumbent's promotion, under the incumbent's own parameters
        incumbent = ledger.current(args.book)
        realized = book.realized_r_since(
            incumbent.timestamp if incumbent is not None else None,
            incumbent.params if incumbent is not None else None,
        )

        result = run_cycle(
            book=args.book,
            ledger=ledger,
            trigger=any_of(ScheduleTrigger(cadence=args.cadence),
                           DriftTrigger(breach_level=args.breach_level)),
            reoptimize=book.reoptimize,
            realized_r=realized,
            now=datetime.now(),                    # the ONLY clock read in the system
            cadence=args.cadence,
        )
    except Exception as exc:                       # noqa: BLE001 - cron needs the message
        print(f"[orchestrate] cycle FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    # A cycle that neither fired nor fell behind is a non-event. Under --quiet it prints
    # nothing at all, so cron (which mails only when a job produces output) stays silent
    # until there is a decision, a skipped window, or a failure worth reading.
    if args.quiet and not result.fired and not result.missed:
        return EXIT_OK

    print(f"[orchestrate] {result!r}{' (dry run, nothing written)' if args.dry_run else ''}")
    for reason in result.reasons:
        print(f"  - {reason}")

    if result.entry is not None and result.entry.action == "halt":
        return EXIT_HALT
    if result.missed:
        return EXIT_MISSED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
