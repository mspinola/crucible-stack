# The toolchain, end to end

How an idea becomes a deployed book, and why the path has this shape.

`architecture.md` describes the *pieces*; this describes the *journey*. If you have been away
from this for six months, start here.

---

## The thesis

Bob Pardo's walk-forward process is the backbone: optimize on a rolling window, validate
out-of-sample, roll the winner forward, repeat. Working through it surfaced one gap — **Pardo's
process has no statistical-validity layer.** Pass/fail is magnitude and consistency; never
significance, and never the cost of having searched.

That cost is the whole problem. Try forty configurations, keep the best, and you have a number
that looks like an edge and is partly just the maximum of forty draws. Try forty markets and
keep the best market, and you have the same problem one level up.

So: **Pardo's operating system, with a significance gate at every step.** Each layer answers one
question, and refuses to answer the next one:

| Layer | Question | Refuses to ask |
|---|---|---|
| **optimizer** (`crucible_stack.optimize`) | which configuration, and what did the search cost? | is it real? |
| **crucible** (public, MIT) | is the edge real, corrected for the search? | what would it earn? |
| **capital sim** (`crucible_stack.capital`) | what does an account trading it look like? | should we deploy it? |
| **orchestrator** (`crucible_stack.orchestrate`) | is it still right, and may it go live? | — |

The refusals matter as much as the answers. crucible never sees currency, so it cannot be
tempted to call a big number an edge. The capital sim runs *downstream* of the verdict and never
re-litigates it — read an equity curve **with** its verdict, never alone. The orchestrator can
act, so it is the only layer that may promote, and only through the gate.

## The seams

The layers connect through four data contracts, and those contracts — not the implementations —
are the invariant. See [seam-contracts.md](design/seam-contracts.md).

```
your data → signal → optimizer ──TrialMatrix──► crucible ──TradeLog──► capital sim ──EquityResult──► orchestrator
   ▲                                                                                                    │
   └────────────────────────── DeploymentLedger.current(book) ──────────────────────────────────────────┘
```

The feedback edge is what makes it a loop rather than a pipeline. The seams are also why the
whole stack was built without a heavy backtesting engine, and why one could still be slotted in
behind them. (The decision to wrap a heavy engine was taken and then superseded on exactly
that evidence, in the strategy repo where this framework was extracted from.)

**`TradeLog` is the pivot.** It is capital-free and denominated in R — one unit of risk. That is
what lets crucible judge an edge without knowing your account size, and it is why the
1R↔currency conversion is pinned to a single place (`EquityResult.meta["r_denominator"]`) rather
than smeared across layers.

---

## The path

### 1. Search, and record what the search cost

```python
from crucible.validation import SearchSpaceLog
from crucible_stack.optimize import sweep, select

log = SearchSpaceLog(scope="GC:gate")          # one ledger for the WHOLE search
tm = sweep(frame, entries, {"gate": ["C", "CL", "CLS"]},
           side="long", tp=2.0, sl=1.0, timeout=56, freq="ME", search_log=log)
```

`sweep` records every configuration **before** it runs, so one that errors or produces too few
trades still counts. That is the honest denominator: those configs cost you a look at the data.

**Share one ledger across sweeps if the search spans several.** Scanning a universe and keeping
the best is a search over the universe. Without a shared ledger each market is corrected as if
it were the only one tried — see *When it says no*, below, for what that costs.

### 2. Get the verdict

```python
sel = select(tm, objective="expectancy")   # or "sharpe" (default), "mean_r", or a callable
print(sel)
for r in sel.reasons:
    print(" -", r)
```

Three corrections run, and `trustworthy` is the AND of those that could be computed:

- **PBO** — did in-sample winning carry out-of-sample, or is the search overfit?
- **the search correction** — is the winner's statistic significant given how many were tried?
- **reality check** — is the edge distinguishable from zero at all?

**The correction follows the objective.** Select on Sharpe and it deflates a Sharpe; select on
expectancy and it corrects with SPA on per-trade R. Correcting a mean-based search with a
Sharpe-based test would price a search nobody ran. `Selection.coherent` is False only for a
custom scorer, where the statistic being optimized is unknowable.

### 3. Size it — but only after the verdict

```python
from crucible_stack.capital import simulate_equity, equity_bands

res = simulate_equity(winner_log, starting_capital=100_000, risk_pct=0.01)
bands = equity_bands(winner_log, risk_pct=0.01)     # block-bootstrap MC envelope
```

This is where currency first exists. A single equity curve is one draw; `equity_bands` shows the
distribution it came from, which is usually the more honest picture. It is routine for a
comfortable-looking terminal figure to sit inside a 5-95 band spanning an order of magnitude,
with a double-digit chance of ending underwater. The point curve is the least informative number
on the chart.

### 4. Deploy, and keep deciding

```bash
python3 -m crucible_stack.orchestrate --book my_book --ledger var/deployments.jsonl \
        --book-factory my_strategies.books.trend:build --cadence 6
```

The loop re-optimizes on a cadence, promotes only what clears the gate, holds the incumbent when
it does not, and watches the live book against the MC envelope it was provisioned with. It does
not trade — it decides which parameters are live and records that decision.
[orchestrate.md](orchestrate.md) is the runbook.

---

## When it says no

The stack's most useful behaviour is refusal, and it is worth seeing what that looks like.

Take a scan of 45 markets with 3 configurations each. Two come back at 98% and 99% deflated
Sharpe, which looks decisive. Priced against the whole 129-variant search they are **0% and 2%**
— and chance alone predicts about 2.2 false passes at a 5% level over 45 markets, against the 2
observed. The passes carried essentially no information.

Two inflations stack to produce that, and both are worth recognizing:

1. **Denominator too small** — each market was corrected for its own 3-gate search and was blind
   to the other 44. Fixed by sharing one `SearchSpaceLog`.
2. **Dispersion too small** — three configs scoring similarly make the null's expected maximum
   tiny, which flatters the winner. Still true within a small search; read a 100% deflated
   Sharpe as "not the binding constraint here", not as certainty.

**Which configuration wins depends on the objective, and that is not a detail.** A filter that
improves per-trade edge often cuts trade count, which makes the monthly series lumpier and scores
worse on Sharpe while scoring better on expectancy. Both variants can clear the gate while
disagreeing about which is best. That is a "which config" question rather than a "does it
qualify" question, and it is why the objective is pluggable (`OBJECTIVES` in `optimize.select`,
each carrying the correction that matches it). Choose the objective before you look at the
answer, not after.

## Things that will bite you

- **A one-config search cannot be judged.** The correction estimates the spread of trial scores,
  and one trial has none. Two configurations minimum.
- **`honest_n` is read when you ask for it.** A verdict taken mid-scan is priced for the search
  so far. Price the winner once the whole search is done.
- **A drift envelope built on the same history it is measured against is in-sample.** It
  demonstrates the machinery, not the book's health.
- **Strategy layers often read a data store at import time**, so a missing environment variable
  can surface as an import or collection error rather than a clear message. Import your strategy
  code lazily inside the methods that need it.

## Where to go next

| Question | Document |
|---|---|
| What are the pieces and how do they depend? | [architecture.md](architecture.md) |
| How do I run and operate the deployment loop? | [orchestrate.md](orchestrate.md) |
| What are the data contracts between layers? | [seam-contracts.md](design/seam-contracts.md) |
| Why is it built this way? | [the ADRs](adr/) |
