# ADR-0007: cotdata downloads COT, all bar data lives in marketdata

> **Scope.** Like ADR-0006, this is a data-layer architecture decision recorded in the toolchain
> ADR log because it sets the story the whole stack reads through. Code changes land in `cotdata`,
> a new `marketdata` sibling, and four consumers. Unlike ADR-0006 it **does** change a consumer
> read contract, which is most of its cost.

**Status:** Accepted (2026-07-27). **Implemented 2026-08-09, with one part of the decision
unimplemented: the databento provider did not move.** Steps 1 to 4 are otherwise done and every
consumer reads bars from `marketdata`. The original acceptance was as a *direction*, on the
strength of step 1 landing cleanly, with the disruptive move still ahead — a deliberate contrast
with ADR-0006, which was accepted after end-to-end validation. That move has now happened. See
[Status of the work](#status-of-the-work) for what shipped and for the one deviation, which is
outstanding work rather than a boundary this ADR drew.
**Date:** 2026-07-25 (accepted 2026-07-27)
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

> **What "and nothing else" does not exclude.** The narrowing here is along the axis of *instrument
> domain* (COT versus bars), not *derived versus raw*. [ADR-0008](ADR-0008-cot-vintage-provenance-in-parquet.md)
> settles the first case to test that reading: as-published (vintage) provenance for the COT data
> is **inside** this boundary, because it is CFTC-positioning provenance and it has to sit beside
> the fetch it records. Read that ADR before concluding that a new `cotdata` subsystem contradicts
> this one.

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

1. **DONE (2026-07-26).** Make the seam explicit **inside** `cotdata`: separate store domains,
   separate manifests, separate CLI entry points.
2. **DONE (2026-08-09), except databento.** Move the price half into `marketdata` as the
   `futures` domain, beside the `equities` domain already built, taking the contract-specs table
   and its `--metadata` flag with it. With step 1 done this is a file move plus a shim.
3. **DONE (2026-08-09).** Migrate consumers one repo at a time behind the shim, `livebook` last.
   Before touching `costs.py`, give it a positive assertion that the specs table loaded, so the
   migration cannot pass silently on zero costs. **The npf half of that guard is built** (npf#159).
4. **VOID** — no shim was built. Remove the shim after a deprecation window. Optionally converge
   both packages on one store root.

Step 1 delivers most of the clarity at a fraction of the risk and is worth doing on its own. That
prediction held: it landed without touching a single consumer read.

Two of step 2's predictions did not. It was **not** "a file move": `cot.py` importing only
`store` made the *seam* clean, but the Norgate provider had to be rewritten against the
tier-aware store rather than copied, since one frame per symbol cannot hold `backadj` and `unadj`
at once. And no shim was built or needed — see Status of the work.

## Status of the work

Updated 2026-08-09.

**Steps 1 to 4 are complete, with one exception recorded below.** `cotdata` 0.4.0 ships with no
bar API, no Norgate and no yfinance; every consumer reads `marketdata.get_bars`.

| Step | State | Where |
|---|---|---|
| 1. Make the seam explicit inside `cotdata` | done 2026-07-26 | `cotdata` #55, #56, #59, #61 |
| 2. Move the price half into `marketdata` | done 2026-08-09, **minus databento** | `marketdata` #7, #8, #10; `cotdata` #103, #104, #105 |
| 3. Migrate consumers | done 2026-08-09 | `cotmetrics` #11, `cot-analyzer` #26, `npf` #196 and #197, `livebook` #10 |
| 4. Remove the shim | **void** — no shim was built, see below | — |

Contract specs moved with the producer as the 2026-07-26 amendment requires: `marketdata` owns
`metadata/contract_specs.parquet` and the `--metadata` flag, and `cotdata`'s
`store.{write,upsert,read}_metadata` are gone. `marketdata` is published as **`crucible-marketdata`**
(the import stays `marketdata`; the obvious distribution name is taken on PyPI by an abandoned
2020 project), which forced a dependency rename in `cotmetrics` #12 and `cot-analyzer` #28.

### The one deviation: databento did not move

**This ADR says it should have.** Two passages, both explicit:

> Norgate futures and databento are not two things. They are two providers of the same thing …
> Separating them would break the abstraction they were built to share.

and step 2's own inventory names `providers/databento.py` (1015 lines) as the **largest single
file** that moves. So databento remaining in `cotdata` is a deviation from the decision, not an
exception the decision carved out, and it should not be read as one.

**Why it stayed.** Not for an architectural reason. It was neither ported nor deleted:

- **Porting it is its own piece of work.** It is not a file move. It is a two-stage paid-API
  producer with an append-only raw bronze store, its own ingest manifest, a two-directional
  reconcile path, a windowed statistics fetch and a build-stage unit scale — around 1,100 lines
  with no counterpart in `marketdata`, whose futures provider is Norgate-shaped.
- **Deleting it would have destroyed working, validated code with no replacement.** ADR-0006 is
  Accepted on the strength of databento's end-to-end validation against Norgate, and it is the
  fleet's only intraday-capable source. A deletion in service of a boundary would have thrown
  that away to make a package description true.

So it was left in place as the lesser of two bad options. **It is outstanding work.**

**What it costs, concretely.** `cotdata`'s *public API* is COT-only; its *store* is not. Keeping
databento means keeping `store.write_prices` / `read_prices`, `config.prices_dir()` and the
`prices` half of the manifest, because databento writes through all of them. The line this ADR
drew — "`cotdata` keeps CFTC positioning and nothing else" — is now true of what a consumer can
import and false of what the store holds. Two packages can still write futures bars, which is
the coupling the contract-specs amendment argued against under "one vendor integration, one
home". And it sharpens the third open question below rather than leaving it where it was: a
databento-sourced dashboard would read bars from `$COTDATA_STORE/prices/` while everything else
in the fleet reads `$MARKETDATA_STORE/bars/futures/`.

**What it does not cost.** Nothing is broken today. databento is not the source for any deployed
consumer — the dash server is on synced Norgate per ADR-0006's Outcome — so the split store is a
latent inconsistency rather than a live one.

### No shim was built, and none was needed

The Consequences section planned "a re-export shim and a deprecation window rather than a clean
cut", and step 4 was to remove it. **The cut was clean.** Ordering made the shim unnecessary and
would have made it harmful:

- By the time `cotdata` #105 deleted `get_prices`, every consumer had already been repointed and
  verified, so a shim would have protected nobody.
- A shim forwarding to `marketdata` would have been fine; a shim left reading `cotdata`'s own
  store would not. That store is no longer filled by the nightly job, so the failure mode is a
  number that looks right and is months old, which is strictly worse than an `AttributeError`.
  `cotdata` therefore asserts in a test that `get_prices` and `roll_dates` stay gone.

Step 4's shim half is void. Its optional second half — converging the two packages on one store
root — is untouched and stays in Open questions.

### Two measurements from the ADR that came out differently

**The move was smaller than predicted, because databento stayed.** Step 2 estimated roughly 1,700
lines moving. About 700 did: `providers/norgate.py` (469, arriving as 546), `prices.py` (167) and
`providers/yfinance.py` (70). databento's ~1,100 did not, so `cotdata` remains roughly 18% price
code by line — the mismatch this ADR was written to remove is reduced, not eliminated.

**`propadj` got stronger than "derived on read".** The second open question is resolved and then
some: `marketdata` derives it from the two stored tiers and **refuses** when only one is present,
rather than returning an empty frame. The producer writes both tiers or neither. That closes the
failure the Treasury seasonal hit, where a wrong tier passed a spot check — additive
back-adjusted percent volatility is ~200x too high for soybeans and **0.47x** for gold, and 0.47x
never goes negative.

### A process finding worth carrying to the next extract

Step 2 ported the Norgate provider **without its tests**, and the gap was invisible for two weeks
because the test *files* existed on both sides. Diffing the test *names* across the 30 that
`cotdata` was about to delete gave **zero overlap** — which reads like a renaming and was, under
it, seven behaviours that would have had no test anywhere the moment the deletion landed
(volume reconstruction, the volume-rank contract pick, the incremental window, the full rebuild,
the NDU-down abort, the all-null spec-row skip). Found only because §7.5 went to delete them, and
fixed in `marketdata` #13.

The same shape appeared twice more in the same step. `finals_ready()` was ported in step 2 and
wired to no CLI flag, so `cotdata`'s `--require-final` was its only caller and deleting that would
have silently ungated the nightly capture (`marketdata` #12). And two databento parity harnesses
read the Norgate store **by path**, so no call-site grep found them.

The lesson generalises past this ADR: **for an extract in the ADR-0004 style, a call-site census
is not a coverage census.** Count what the tests assert and what reaches each entry point, not
what files exist.

## Open questions

Updated 2026-08-09. Two of the three below are resolved; the rest is what remains.

- **Port databento into `marketdata`, or accept the split permanently.** The live one, and the
  only unimplemented part of the Decision (see Status of the work). Until it is settled,
  `cotdata` holds a price store it has no consumer API for. Accepting the split permanently is a
  legitimate answer, but it needs writing into the Decision as an amendment rather than being
  left to read as unfinished work.
- **Whether the two packages converge on a single store root env var, and when.** Unchanged and
  still cheap, but no longer only cosmetic: a launcher now has two roots to get right, and one
  has already been found half-wired (below).
- ~~Whether the futures `propadj` tier stays derived-on-read.~~ **Resolved.** It stays derived,
  and `marketdata` hardened it: deriving from one stored tier raises rather than returning empty,
  and the producer writes both tiers or neither. The evidence that decided it is unchanged — the
  month-end Treasury seasonal's first run was VOIDED because `backadj` percent returns
  sign-inverted 15 of ZB's 100 trades.
- **Whether the Linux server runs the `marketdata` futures producer itself, or keeps receiving a
  synced store.** Still open, and the databento deviation sharpens it: a databento-sourced server
  now reads bars from a *different package's store* than every other consumer. The dash is on
  synced Norgate per ADR-0006's Outcome, so this is latent rather than live.

Found while implementing, and not previously tracked here:

- **`livebook/bin/daily.sh` guards `COTDATA_STORE` and not `MARKETDATA_STORE`.** The live job's
  bars and contract specs both come from `marketdata` now, so the guard checks the store that
  matters less. It fails closed rather than silently — `marketdata.config.store_root()` raises by
  name on an unset root, and `livebook.specs.broker_specs` asks at `required=True` — so the
  consequence is a traceback mid-run instead of a clear refusal at the top. Worth making
  symmetric.
- **The Status section's note that "`livebook` is NOT covered" for contract specs is out of
  date.** `livebook.specs.broker_specs` now calls `contract_specs(required=True)` and asserts the
  `Exchange` column resolves before the broker connects, which is the guard step 3 asked for.
  `npf.validation.costs` resolves the table unambiguously from `marketdata` now that `cotdata`'s
  copy is gone, and reports which package answered.
