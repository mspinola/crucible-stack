# ADR-0007: cotdata downloads COT, all bar data lives in marketdata

> **Scope.** Like ADR-0006, this is a data-layer architecture decision recorded in the toolchain
> ADR log because it sets the story the whole stack reads through. Code changes land in `cotdata`,
> a new `marketdata` sibling, and four consumers. Unlike ADR-0006 it **does** change a consumer
> read contract, which is most of its cost.

**Status:** Proposed (2026-07-25)
**Date:** 2026-07-25
**Deciders:** Matt (sole maintainer)

## Context

`cotdata` answers two unrelated questions: what does the CFTC report say about positioning, and
what did this instrument trade at. The second arrived later and has quietly become the larger half.

The trigger was scoping equity and ETF bars. That work cannot live in `cotdata` at all, because
`load_registry` refuses any symbol without a `cftc_code` and equities have no COT report. Having
drawn that line once, the line between COT and futures bars is the same line for the same reason.

Four measurements taken while scoping, all of which shrink the problem:

1. **The package is already mostly prices.** Roughly 1,700 lines of price code against roughly 550
   lines of COT code, plus 830 lines of shared plumbing. The package name describes its smaller half.
2. **The seam is already clean.** `cot.py` imports only `store` and `registry`. `prices.py` imports
   only `store`. No cross-imports exist between the two halves, and the CFTC providers never
   reference a price module. Nothing needs untangling. Files move.
3. **The registry is effectively private.** It is exported in `__all__`, but no consumer across
   `cotmetrics`, `cot-analyzer`, `npf` or `livebook` imports it. Consumers touch only `get_prices`
   (27 call sites), `config` (7), `get_cot` (6), `schema_version` (2) and `store` (1, which is
   `read_metadata`, see the contract-specs section below).
4. **Almost nothing joins the domains.** Exactly two files read both prices and COT.

There is also a platform boundary living inside the package and invisible from outside it. The CFTC
download is free and runs on any OS. The Norgate producer runs only on Windows.

## Decision

**`cotdata` keeps CFTC positioning data and nothing else. All bar data, every instrument domain and
every vendor, moves to one sibling package: `marketdata`.**

### Contract specs move with the producer, not with the COT

Amended 2026-07-26. The original text said only that "all bar data" moves, which left a third
output unaccounted for.

The Norgate producer writes **two** things, not one. Alongside the bars it writes contract specs
(Name, Exchange, Group, Contract Size, Tick Size, Tick Value, Point Value, Currency, Margin) into
`metadata/contract_specs.parquet`, behind its own `--metadata` CLI flag. Norgate is the sole
producer of that table. No other provider writes it.

Contract specs are neither COT nor bars, so the original wording did not place them. They move to
`marketdata` for two reasons. By this ADR's own rule `cotdata` keeps CFTC positioning **and nothing
else**, and a tick value is not CFTC positioning. And splitting the producer in half would leave
two packages both importing `norgatedata` and both requiring the Windows host, which is precisely
the coupling this split exists to remove. One vendor integration, one home.

`metadata/` therefore becomes a `marketdata` store domain, and `--metadata` becomes a `marketdata`
CLI flag.

### Vendors are providers, never packages

Norgate futures and databento are not two things. They are two providers of the same thing:
continuous futures bars, same output contract, same adjustment axis. ADR-0006 exists precisely to
say one of them owns a given symbol at a time. Separating them would break the abstraction they were
built to share. The same holds for Yahoo and Norgate on equities.

### The axis is the instrument domain, and it stays inside one package

| Domain | Adjustment axis | Providers |
|---|---|---|
| `futures` | roll splicing (`backadj`, `unadj`, `propadj`) | Norgate (Windows), databento (any OS) |
| `equities` | corporate actions (`raw`, `split`, `total`) | yfinance (any OS), Norgate stocks (Windows) |

An earlier draft of this ADR put these in two packages, arguing they "share nothing in the
adjustment axis". That reasoning does not hold. Two modules in one package that do not import each
other is ordinary modularity. What they do share is the store, the registry, the manifest, the CLI,
vendor resolution and the producer/consumer discipline, which is nearly everything else. The
premature-abstraction argument applies to extracting a shared core from two packages that already
exist. It says nothing about whether to split one package in the first place.

### databento is deployment-scoped, not a general Norgate alternative

databento exists for exactly one reason: the `cot-analyzer` dashboard runs on a Linux server that
cannot run Norgate. That is the problem ADR-0006 was written to solve, and it remains the whole of
databento's remit. It is **not** a second research vendor, and its coverage should not be broadened
toward parity with Norgate.

Three facts bound the obligation tightly, and they are worth recording so nobody later mistakes
narrowness for an omission:

1. **Nothing in `cot-analyzer` calls the price API directly.** It reaches prices transitively
   through `cotmetrics` (`CotIndexer.py`, `signals.py`, `options_data.py`).
2. **Every one of those reads is `adjustment="backadj"`.** So databento owes exactly one series per
   symbol, not the full tier set.
3. **The universe is the deployed `params.yaml`,** not the whole futures registry.

Consequences. databento stays behind an optional extra, as it already is in `cotdata`, so no local
research install pays for it. Its two-stage raw store stays producer-internal and excluded from any
consumer sync. Markets outside CME Globex keep falling back to Yahoo rather than growing bespoke
databento support. And local research stays on Norgate with its deeper history, which is the
asymmetry `resolve_source` was built to express.

### The domain is a registry fact, not an argument

```python
get_bars("ES",  "backadj")   # registry says futures -> roll axis
get_bars("SPY", "total")     # registry says equities -> corporate-action axis
```

Asking for a futures tier on an equity symbol raises and names the valid ones. One registry keeps
`ES` meaning one thing, which also removes any need for a cross-repo symbol-agreement test.

### Store layout

The vendor and the domain are both path components, not manifest-only labels:

```
$MARKETDATA_STORE/
  bars/futures/norgate/ES_backadj.parquet
  bars/futures/databento/ES_backadj.parquet
  bars/equities/yfinance/SPY.parquet
  bars/equities/norgate/SPY.parquet
  metadata/contract_specs.parquet
  _raw/databento/ingest_manifest.json
  manifest.json
```

`metadata/` sits outside `bars/` because it is one table keyed by symbol rather than one file per
symbol, which is also why a scoped refresh must upsert rather than replace.

Two vendors that both carry a symbol would otherwise write one file and the producer that ran last
would silently win. On equities the overlap is total rather than incidental, and the vendors do not
even store the same columns. Separate paths also make a vendor A/B comparison possible, which is how
a free feed gets validated against a paid one.

A provider's `NAME` must be a member of `registry.PRICE_SOURCES`, because the same string is both a
path component and what `resolve_source` returns. A read that finds nothing under the resolved
vendor but finds the symbol under another raises and names the alternatives, rather than falling
back. Falling back would blend two vendors across a re-run, which ADR-0006 forbids.

### Separate manifests, not separate roots

The read-modify-write hazard in `_touch_manifest` is solved by one manifest per writer, not by
splitting store roots. `cotdata` already proves this: its databento provider writes
`_raw/databento/ingest_manifest.json` beside the main `manifest.json` in one root. Both packages may
therefore live under one synced parent folder, so there stays one thing to back up.

## Consequences

**What it costs.** `get_prices` has 27 call sites across four repos, one of which (`livebook`) runs a
live weekday-morning job against an append-only ledger. `cotdata` is published on PyPI, so the price
API needs a re-export shim and a deprecation window rather than a clean cut. Every launcher gains a
second store variable until the roots converge.

The shim covers **two** symbols, not one. `read_metadata` moves with the contract specs, and its
blast radius is different from the bars migration:

| Symbol | Consumers |
|---|---|
| `get_prices` | `cotmetrics` (and `cot-analyzer` transitively), `npf`, `livebook` |
| `read_metadata` | `npf/src/npf/validation/costs.py:34`, and `livebook/bin/flatten.py` via it |

`costs.py` uses Point Value and Tick Value to convert R-multiples into dollar costs, and degrades to
zero costs with a warning when the table is unavailable. That fallback is a hazard during migration:
a broken import produces a silently cost-free backtest rather than an error, so the specs move needs
a positive assertion that the table loaded, not just a green test run.

**What it buys.** A package whose name describes what it does. A cross-platform COT downloader with
no Windows-only dependency in its tree. One bar package where a lesson learned on one domain applies
to the other. And a data layer matching the chain-of-refusals principle the rest of the toolchain
follows, where each layer answers one question and refuses the next.

**What it does not change.** ADR-0006 survives intact and gets stronger, because a domain-and-vendor
path makes accidental contention for one file impossible rather than merely unlikely.

## Alternatives considered

**Leave it alone.** Genuinely viable. Nothing is blocked today. The cost is that the mismatch
compounds every time the price side grows.

**One repo per vendor.** Rejected. Vendors are an implementation detail behind `resolve_source`, and
a Norgate repo and a databento repo would produce the identical artifact.

**Two bar packages, futures and equities.** The previous draft of this ADR. Rejected above.

**A shared registry package.** Rejected. Around 210 lines does not justify a package, and it would
recreate the coupling this ADR removes.

## Sequencing

Extract in the ADR-0004 style, so the disruptive step is decoupled from the design decision.

1. Make the seam explicit **inside** `cotdata`: separate store domains, separate manifests, separate
   CLI entry points.
2. Move the price half into `marketdata` as the `futures` domain, beside the `equities` domain
   already built, taking the contract-specs table and its `--metadata` flag with it. With step 1
   done this is a file move plus a shim.
3. Migrate consumers one repo at a time behind the shim, `livebook` last. Before touching
   `costs.py`, give it a positive assertion that the specs table loaded, so the migration cannot
   pass silently on zero costs.
4. Remove the shim after a deprecation window. Optionally converge both packages on one store root.

Step 1 delivers most of the clarity at a fraction of the risk and is worth doing on its own.

## Status of the work

`marketdata` exists with the `equities` domain, the yfinance provider, the three-tier adjustment
derived on read, and a pin test reproducing the vendor's own adjusted column. The `futures` domain
is declared in the adjustment map so error messages are correct from the first day, but has no
provider yet. Nothing in `cotdata` has moved.

## Open questions

- Whether the two packages converge on a single store root env var, and when.
- Whether the futures `propadj` tier stays derived-on-read as it is today.
- Whether the Linux server runs the `marketdata` futures producer itself after step 2, or keeps
  receiving a synced store. databento is not currently declared in `cot-analyzer`'s own
  requirements, so that wiring is unfinished either way.
