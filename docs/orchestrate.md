# Running the re-optimization loop

Operating guide for `crucible_stack.orchestrate`, the deployment orchestrator. For *why it is built
this way*, see [ADR-0003](adr/ADR-0003-deployment-orchestrator-as-stateful-reoptimization-loop.md);
for the data contract it persists, see [seam-contracts.md](design/seam-contracts.md) (Seam 4).

> **Not to be confused with `core.walk_forward_engine.WalkForwardOrchestrator`**, which runs a
> strategy across rolling windows and hands back a result. That is a *lens*, you call it, you
> read the answer, nothing persists. This is the opposite: a long-lived loop that holds state
> and decides what your live book should be trading.

---

## The problem it solves

A strategy's parameters are fit to a window of history. Markets move; the window slides; the
parameters go stale. Nothing announces this. The book keeps trading, the numbers drift, and the
question *"are these still the right parameters?"* only gets asked when something looks wrong,
which is exactly when you are least able to answer it calmly.

Walk-forward analysis is normally run **once**, as a validation gate: *did this survive
out-of-sample?* Pardo's actual practice is to run it **continuously**, periodically re-optimize
on the most recent window and roll the winning parameters forward into live trading. That is a
live operating system, not a one-time test, and this package is that system.

## Why use it instead of re-fitting by hand

You could re-fit every six months yourself. Four things make that go wrong, and each is
something the loop does structurally:

**It can refuse.** A re-optimization that does not clear the honesty gate cannot be deployed,
the loop keeps the incumbent parameters and records why. Hand re-fitting has no such brake: you
re-fit, the numbers look good *because you just fit them*, and you deploy. "The new parameters
are not trustworthy, stay on the old ones" is a first-class outcome here, not an act of
discipline you have to remember.

**It counts the searches.** Every re-optimization is more variants in the `SearchSpaceLog`, so
the multiple-testing denominator grows. Re-fitting quarterly for five years is twenty searches,
and twenty searches is a much higher bar than one. The loop knows that; a person re-fitting by
hand almost never does.

**It watches for drift.** Not just *"has the calendar rolled?"* but *"has the live book left the
envelope it was provisioned with?"*, measured against the simulation's own block-bootstrap
bands rather than a threshold someone picked. If the live curve escapes the band it was
deployed within, the loop re-optimizes early instead of waiting out the cadence.

**It leaves an audit trail.** Every decision is recorded, including the refusals, with the
verdict and the evidence behind it. *"Why are we still on the old parameters?"* has an answer,
months later, without re-running anything.

## What the cron job is actually for

**It is a heartbeat, not a batch job.** Cron wakes the loop up; the loop decides whether
anything needs doing. Most invocations do nothing at all, and that is the design working, a
cycle that does not fire writes nothing to the ledger, because a non-event is not a decision and
one entry per cron tick would bury the real ones.

Cron is doing exactly two things:

1. **Asking the question on a schedule you will not forget.** The whole failure mode above is
   "nobody remembered to check."
2. **Giving drift somewhere to be noticed.** The drift trigger can only fire if something looks.

**The cron job does not trade.** It places no orders and sizes no positions. It decides which
parameter set is live and records that decision; your trading path reads
`ledger.current(book)`. Keeping the decider and the executor separate is deliberate, this
process can be killed, restarted, or skipped entirely without touching a position.

---

## Quick start

```bash
MY_DATA_STORE=/path/to/store .venv/bin/python -m crucible_stack.orchestrate \
  --book my_book \
  --ledger var/deployments.jsonl \
  --book-factory my_strategies.books.gold:build \
  --cadence 6
```

A crontab entry, monthly, 06:30 on the 1st:

```cron
30 6 1 * * cd /path/to/your/repo && mkdir -p var && MY_DATA_STORE=/path/to/store \
             .venv/bin/python -m crucible_stack.orchestrate --book my_book \
             --ledger var/deployments.jsonl --book-factory my_strategies.books.trend:build \
             --cadence 6 --quiet >> var/loop.log 2>&1
```

**`mkdir -p var` is not decoration.** The shell redirect is set up *before* Python runs, so
if `var/` does not exist the job dies with exit 1 and **no log, because the log is what
failed.** The ledger creates its own directory; the redirect cannot.

**`--quiet` is what makes the exit codes mean anything.** cron mails you when a job produces
output, and it ignores exit status entirely, so with everything redirected to a log, an
unattended job is silent even when it HALTs. Under `--quiet` a cycle that neither fired nor
fell behind prints nothing at all, so you can drop the redirect (or keep it) and hear from
cron only when there is a decision, a skipped window, or a failure.

| Flag | Meaning |
|---|---|
| `--book` | Key on the ledger. One ledger can hold many books; this names which one. |
| `--ledger` | Path to the append-only JSONL file. Created if absent. |
| `--book-factory` | `pkg.module:callable` returning the book wiring (see below). |
| `--cadence` | Scheduled re-optimization cadence, **in months** (default 6). |
| `--breach-level` | Quantile counting as a drift breach (default: the envelope's lowest). |
| `--dry-run` | Evaluate and report, write nothing. |
| `--quiet` | Say nothing when nothing happened, so cron only mails you on a decision, a skipped window, or a failure. |
| `--status` | Report what is live and exit. Runs no cycle, needs no book factory and no data store. |

## Checking what is live, without running anything

```bash
python3 -m crucible_stack.orchestrate --status --book my_book --ledger var/deployments.jsonl
```

```
[orchestrate] my_book  (1 decision(s) on var/deployments.jsonl)
  LIVE: {'fast': 20, 'slow': 100, 'filter': True}
    promoted   : 2026-07-22 (0d ago, ~0.0 months)
    verdict    : TRUSTWORTHY (honest_n=10)
    envelope   : 481 periods, frozen at promotion
    caveat     : selected and corrected on DIFFERENT metrics (see reasons)
  history: promote=1, hold=0, halt=0
```

Reads the ledger only, no book factory, no `MY_DATA_STORE`, no simulation. It also prints
`OVERDUE` when the incumbent is more than a cadence past due.

**You do not have to run the loop on a schedule.** It tracks its own lateness: run a cycle
whenever you think of it and it will tell you how many windows were skipped, still decide
correctly, and exit `4` to say it was late. A schedule buys you the reminder, not the
correctness. Running it irregularly is safe, just noisier.

## Reading the result

```
[orchestrate] CycleResult('my_book' HALT, missed=0)
  - schedule: no incumbent is live; the loop has nothing to preserve
  - gate: REFUSED - selection is NOT trustworthy
  - gate: HALT - no incumbent to fall back on; staying flat
  - deflated_sharpe below bar (92% vs >= 95%)
  - reality FRAGILE
```

### Exit codes

| Code | Meaning | What to do |
|---|---|---|
| `0` | Cycle completed, no-op, hold, or promote | Nothing. |
| `3` | **HALT**, nothing is safe to trade | Look. The candidate was refused and there is no incumbent to fall back on, so the book is flat. |
| `4` | A scheduled window was **skipped** | The job did not run when it should have. Check cron, the machine, the log. |
| `1` | The cycle raised | Read stderr; the exception type and message are printed. |

### The three actions

- **`promote`**, the candidate cleared the gate and is now live.
- **`hold`**, the candidate was refused; the incumbent keeps trading.
- **`halt`**, the candidate was refused and there *is* no incumbent. Nothing is live. This is
  not a failure state, it is the honest one: on a cold start with an unproven book, flat is
  correct.

### An entry on the ledger

```json
{"book": "my_book", "action": "halt", "trigger": "schedule+drift",
 "verdict": "NOT TRUSTWORTHY", "trustworthy": false, "honest_n": 3,
 "params": {"gate": "C"}, "fit_window": ["2004-01-05", "2026-07-20"],
 "envelope": null, "equity_ref": null, "reasons": [...]}
```

- `trigger`, what fired: `schedule`, `drift`, or `schedule+drift` when both did.
- `honest_n`, how many configs the search tried. This is the multiple-testing denominator, and
  it is the ledger's count, not the number that produced results.
- **`envelope` is `null` on `hold`/`halt`, and that is deliberate.** An envelope is the
  authorization artifact of a promotion; a refused candidate provisioned nothing. It also
  closes the last route by which a *rejected* envelope could become the baseline that future
  drift is measured against.
- `equity_ref` points at the capital-sim run; it is not a copy of it.

Read it back in Python:

```python
from crucible_stack.orchestrate import DeploymentLedger
led = DeploymentLedger("var/deployments.jsonl")
live = led.current("my_book")          # most recent promotion, or None
led.history("my_book", action="hold")  # every refusal, with its reasons
```

---

## Wiring a book

The factory takes no arguments and returns an object with two methods:

```python
def reoptimize() -> Reoptimization:
    """Run the search, gate-able. Returns the Selection plus its provenance:
    fit_window, the envelope this candidate would be provisioned with, equity_ref."""

def realized_r_since(since, params) -> Sequence[float]:
    """Periodic R the LIVE configuration produced since it went live.
    `since` and `params` come off the ledger's current entry, the book cannot know
    when its own parameters were promoted."""
```

Two things a book adapter should do, both learned the hard way:

**Produce realized R with the same simulator that built the envelope.** Otherwise drift measures
the difference between two simulators rather than decay in the edge, and reads as drifted on day
one.

**Import your strategy code lazily, inside the methods that need it.** If your strategy package
touches a data store at import time, many do, to resolve a universe or open a cache, then a
module-level import makes the adapter unimportable without that store configured. The missing
environment variable then surfaces as a test-collection error rather than a clear message. This
is also what lets `orchestrate` resolve your book by dotted path without ever importing it.

Book adapters live in *your* repo, never in `crucible_stack.orchestrate`. The orchestrator
resolves them by dotted path and never imports them, which is what lets this framework ship
with no strategy in it (enforced by `tests/test_boundaries.py`).

## Cold start vs steady state

**The first run always fires**, under every trigger policy, even a 99-month cadence. Nothing is
live, so there is nothing to preserve and no reason to wait. It will usually `halt`, because an
unproven book should not go live on its first look.

**Steady state is quiet.** With an incumbent and no drift, the cycle returns a no-op and writes
nothing until the cadence elapses.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `need >= 2 trial Sharpes` | A one-config search. The correction estimates the spread of trial Sharpes and one trial has none, a single config has no multiple-testing cost and cannot be honestly selected. Give the grid at least two. |
| `RuntimeError: MY_DATA_STORE is not set` | The env var is missing. It is read at import time by the strategy layer, so this can look like an import bug. |
| Exit `4` every run | The elapsed period keeps exceeding the cadence, cron is not firing, or `--cadence` is shorter than reality. |
| Drift never fires | Check the incumbent has an `envelope`. With none attached the drift trigger fires *loudly* rather than staying quiet, so silence means it is being evaluated and not breaching. |
| Verdict looks too good | Check `honest_n`. If several sweeps share one search, pass one `SearchSpaceLog` (`sweep(search_log=...)`) or every correction is blind to the rest of the search. |
