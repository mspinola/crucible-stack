# ADR-0004: A public strategy framework; strategies are the only private IP

> **Provenance.** This ADR was written in the private `npf` repository before the framework
> was extracted (ADR-0004), and it is preserved here as the decision record for code that now
> lives in this repo. The reasoning is unchanged and still current. Module paths in the body
> are the pre-move ones, and map as follows:
>
> | as written | today |
> |---|---|
> | `npf.optimize` | `crucible_stack.optimize` |
> | `npf.capital` | `crucible_stack.capital` |
> | `npf.orchestrate` | `crucible_stack.orchestrate` |
> | `npf.framework` | `crucible_stack.framework` |
> | `npf.strategies.simulator` / `.exits` | `crucible_stack.engine.simulator` / `.exits` |
>
> References to `npf` books, COT strategies and the `cotdata` store are examples from the
> repo where this was written. They are not requirements of this framework, which ships with
> no strategy at all.

**Status:** Accepted (2026-07-22)
**Date:** 2026-07-22
**Deciders:** Matt (sole maintainer)
**Revises [ADR-0003](ADR-0003-deployment-orchestrator-as-stateful-reoptimization-loop.md)
action item 7**, which set extraction to fire on "a family of strategy books" — a count test.
This replaces it with a kind test, and draws the line somewhere different besides.

## Context

npf began as a walk-forward validation harness for one strategy family: the CMR positioning
book, its permutations, and an ML path. Everything in it was about that strategy.

It is no longer that. It now also contains a **general framework for defining, validating,
sizing and deploying strategies** — Pardo's walk-forward process with a significance gate at
every step — which happens to have exactly one strategy family living inside it.

**The vision this ADR serves:** a *public* framework for defining strategies — structure, params
schema, interfaces, result verdicts, visualizations — where **the strategies themselves are the
only private IP**. Someone should be able to write a specialised gold trend-following strategy
against this framework and keep its details private if they choose, while every tool they used
is public.

That is a different line than "split the toolchain out of npf". The toolchain is only part of
what a strategy author needs; the *interfaces they write against* matter more.

### Three seams, in decreasing order of readiness

**1. The toolchain is already separable.** Importing all of it drags in **zero** strategy or
application modules:

```
>>> import npf.optimize, npf.capital, npf.orchestrate
strategy/app modules dragged in: NONE          # no npf.strategies, npf.core, npf.ml, cmr, cotmetrics
npf.validation modules pulled in: portfolio_mc, sizing
```

That is not luck. `npf.orchestrate` is pinned by `tests/test_orchestrate_boundaries.py`,
`npf.optimize`'s one strategy touch is a lazy import commented *"keep npf.optimize core-free"*,
and `npf.books` was deliberately placed outside `npf.orchestrate`.

**2. The authoring interfaces already exist, mixed in with implementations.**

| File | Lines | Nature |
|---|---|---|
| `strategies/base.py` | 70 | `RulesStrategy(ABC)`, abstract methods — **interface** |
| `strategies/__init__.py` | 26 | `STRATEGY_REGISTRY`, `get_strategy` — **framework** |
| `core/config_parser.py` | 142 | params schema; contains **no** COT/gate/CMR fields — **framework** |
| `strategies/npf.py` | 149 | `NpfStrategy` — **private IP** |
| `strategies/trend_donchian.py` | 191 | `TrendDonchianStrategy` — **private IP (author's choice)** |

`books/` is already clean the same way: the protocol lives in the toolchain
(`orchestrate.runner.Reoptimization`), with `books/cmr.py` and `books/trend.py` as
implementations.

**3. The rules simulator is the one genuine obstacle.** `strategies/simulator.py` (241 lines)
is spec-driven — `stop_spec` / `exit_spec` — which is framework-shaped. But COT is baked into
its branches, and the default gives it away:

```python
exit_mode = exit_spec.get('mode', 'cot_neutral')            # <- the DEFAULT
...
elif exit_mode == 'willco_neutral':  wc = bar.get('willco', np.nan)
else:  # 'cot_neutral' — ride until the gated trader leg(s) cross ~50
       ci_, li_, si_ = bar.get('comms_idx'), bar.get('lrg_idx'), bar.get('smll_idx')
```

Generic modes (`barriers`, `channel`, `atr_trail`; stops `wick`, `atr`) sit in the same
if/elif chain as COT-specific ones. **A framework whose default exit reads COT columns is not a
general framework.**

**Forces at play:**

- **Two different things share one name.** "npf" means both a strategy and a general framework.
  The README described only the strategy half for as long as both existed.
- **The private surface is narrow.** Only the NPF system's specifics are sensitive: its docs and
  findings, the YAML *values*, and how the COT index is constructed. Verified rather than
  assumed — auditing the toolchain for strategy vocabulary found no embedded findings, no COT
  indexing and no config values, only illustrative CMR references in docstrings.
- **The boundary is cleanest right now.** Two of the three seams are already clean and one is
  enforced by a test. Boundaries nobody has to respect drift.
- **A public framework changes the maintenance contract.** Interfaces others write against are
  promises; today they can be changed at will.

## Decision

Three tiers, split by *what kind of thing it is*, not by which repo it grew up in.

| Tier | Repo | Contents |
|---|---|---|
| **Verdicts** | `crucible` (public, MIT) | capital-free edge statistics, the gauntlet, report primitives |
| **Framework** | `crucible-stack` (public) | how you *define, search, size and deploy* a strategy |
| **Strategies** | `npf` (private) | the strategies themselves and everything that reveals them |

**`crucible-stack` (public) gets:**

| What | Notes |
|---|---|
| `optimize/`, `capital/`, `orchestrate/` | the toolchain — Seams 1, 3, 4 |
| `strategies/base.py`, `strategies/__init__.py` | the `RulesStrategy` ABC and the registry |
| `core/config_parser.py` | the params/config schema |
| `strategies/simulator.py`, **generic modes only** | the rules engine, with a pluggable exit/stop registry |
| `validation/portfolio_mc.py` | the single MC engine |
| `weighted_monthly_returns` **only**, from `sizing.py` | see the correction below |
| `docs/adr/`, `docs/design/`, `docs/toolchain.md`, `docs/orchestrate.md` | the design record |

**`npf` (private) keeps:** `strategies/npf.py`, `strategies/trend_donchian.py`, the COT-specific
exit modes (`cot_neutral`, `willco_neutral`) registered into the framework's registry, the CMR
`validation` pipeline, `ml`, `pure_edge`, `adapters`, `deploy`, `books/cmr.py` and
`books/trend.py`, all YAML config **values**, and every doc that records a finding.

**Dependency direction:** `crucible` <- `crucible-stack` <- `npf`. One way, as with every other
seam in this project. The framework never imports a strategy.

### This is bigger than a file move

The earlier framing of this ADR — "split the toolchain out" — was a move plus two module
relocations. The framework framing is materially larger:

1. **Extract two interfaces** (`RulesStrategy` + registry, `config_parser`) from the packages
   they currently share with implementations.
2. **Build an exit/stop registry** in the simulator, mirroring `STRATEGY_REGISTRY`, so generic
   modes ship public and `cot_neutral` / `willco_neutral` register from the private side. Change
   the default away from `cot_neutral`.
3. Then the move.

Step 2 is the only genuinely new engineering, and it is bounded: an if/elif chain becomes a
lookup, which is a pattern this codebase already uses for strategies.

**Steps 1 and 2 are done** (items 4 and 3, npf #106 and #104). What remains is mechanical:
relocation, wiring, and policy. The framework now imports with no data store and no strategy,
which is the property the move depends on — re-check it at execution time rather than trusting
this sentence.

### Correction: `sizing.py` must not move

An earlier draft moved all 248 lines. It is not framework-generic: its docstrings are about "the
NPF book" and "NPF's deep account DD", it hard-codes known NPF equity-stress windows, and
`load_daily_returns` **imports `cotdata`** — a data-store dependency the framework otherwise does
not have. `npf.capital` needs exactly **one** symbol from it: `weighted_monthly_returns`, ten
lines whose own docstring calls it *"the sized analogue of `portfolio_mc.monthly_returns`"*.
That moves; the rest stays.

The audit that caught this is worth repeating at execution time rather than trusting this
document's file list.

### The trigger was wrong

ADR-0003 item 7 set extraction to fire on *"a family of strategy books"* — a **count** test. The
better test is **kind**: the framework is general and a strategy is not, which was true before
the second book existed. The count test also gets the economics backwards, waiting until the
boundary has had the most time to erode.

## Options Considered

### Option A: public framework, private strategies (recommended)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Med — two interface extractions, one registry, then a move |
| Cost | A second repo's CI; a public API contract to honour |
| Correctness | Puts the line where the IP actually is |
| Reversibility | High — merging two clean repos is easier than splitting a tangled one |

**Pros:** matches the actual shape of the IP; a strategy author gets a complete public toolset;
the framework becomes independently versionable; the name stops meaning two things.
**Cons:** the largest of the options; public interfaces are promises; npf's docs thin out.

### Option B: leave everything in npf

**Pros:** zero work.
**Cons:** the framework is unusable by anyone else, including future-you starting a second
strategy family; the name keeps meaning two things; splitting cost rises monotonically.

### Option C: split only the toolchain (this ADR's first draft)

**Pros:** smaller — a move plus two relocations, no registry work.
**Cons:** ships a deployment stack with no way to *define* a strategy against it. An author would
get `optimize`/`capital`/`orchestrate` and then have to invent their own strategy interface,
config schema and rules engine. It solves the repo-naming problem without serving the vision.

### Option D: fold everything into crucible

Named to reject it. The framework is capital-coupled (layer 2) and stateful with live side
effects (layer 3); both disqualify it under ADR-0003's promotion path, and crucible's value is
that it is small, MIT and `numpy`+`pandas`.

### Option E: `crucible-stack` as a package inside the **crucible repo**

A sibling package, not a merge — `src/crucible/` and `src/crucible_stack/`, two distributions,
one checkout. **Technically sound, with precedent:** crucible already ships a separate
distribution out of `packaging/`, and the purity guard globs `src/crucible` specifically, so a
sibling would not trip it and the published wheel is unchanged.

**Pros:** the strongest argument is ergonomic and was felt repeatedly while building this — a
change spanning both currently needs **two PRs in a forced order**, because npf's CI checks out
crucible's `main`. One repo makes those single PRs, with no version pin and no third editable
install.
**Cons:** crucible's differentiation is being small and obvious at a glance, and a visitor
evaluates the repo before the wheel. Release cadences also differ sharply — a published 0.2.0
library versus a pre-1.0 framework moving weekly.

**Verdict: deferred, not rejected.** Take the name now; keep it a separate repo while the
framework is this young. Revisit if cross-repo PR pairs become the dominant cost — they were 2
of roughly 15 changes in the week this was built.

## Trade-off Analysis

The question is not whether the boundary exists — two of the three seams are already clean and
one is enforced by a test — but whether to draw it at the toolchain or at the framework.

Option C is the tempting one because it is cheapest, and it is the one to resist. A deployment
stack you cannot define a strategy for is a tool for exactly one existing user. The gap between C
and A is one registry and two file extractions; the gap in what it *delivers* is the difference
between "my toolchain, extracted" and "a framework someone else could use".

Option B's cost is invisible and compounding. Option E is attractive and premature: it trades
crucible's clarity for an ergonomic win that has not yet become the dominant cost.

The decisive asymmetry is **direction of regret**. Splitting two clean repos later is a
migration; un-splitting them is a merge, and merges are easy. The cheap moment is while
`portfolio_mc` and `weighted_monthly_returns` are the only entanglement and a test still proves
the rest.

## Consequences

**Easier:**
- npf becomes describable in one sentence again: a strategy family and its research.
- A second strategy family — yours or anyone's — gets a complete public toolset without the CMR
  pipeline attached.
- Whether a given strategy is public becomes the author's per-strategy choice, which is the
  point of the vision.
- The extraction-readiness guard becomes unnecessary: the boundary is the repo.

**Harder:**
- **Public interfaces are promises.** `RulesStrategy`, the config schema and the exit registry
  become an API others write against, and today they change at will. Worth a stability policy
  before publishing, not after.
- Two CIs and a cross-repo version contract; changes spanning both need ordered merges —
  crucible, then framework, then npf.
- **The shared-checkout hazard multiplies.** Three sibling editable installs across concurrent
  sessions is how this week's two incidents happened.
- npf's docs thin considerably; `architecture.md` needs splitting or rewriting in each repo.

**To revisit:**
- If no second strategy family ever materialises, the framework is a public good with one user.
  That is a real possibility and an acceptable one, but it should be a choice rather than a
  surprise.

## Action Items

1. [x] **Name: `crucible-stack`** (decided 2026-07-22). States the relationship, and survives
       either outcome of Option E. `pardo` rejected as historically loaded —
       `pardo_quant_framework/src` *was* the `pardo` package before the rename to npf.
2. [x] **Public/private line settled 2026-07-22.** Only the NPF system's specifics are sensitive
       — docs and findings, YAML values, COT index construction. The framework may be public.
3. [x] **Build the exit/stop registry** in `simulator.py`; move `cot_neutral` / `willco_neutral`
       to the private side and change the default away from a COT mode. **DONE 2026-07-22**
       (npf #104, `a13bed8`) — `strategies/exits.py` holds the registry, the four generic rules
       and the engine defaults; `strategies/cot_exits.py` holds the two COT rules and registers
       them on import of `npf.strategies`. Default is now `barriers`, the only mode needing
       nothing but OHLC. 14 tests.
       - **Only exits needed a registry.** All three stop modes (`wick`, `atr`, `atr_trail`) are
         already generic — none reads a strategy column — so building a stop registry would have
         solved a problem that does not exist. Stops stay a plain branch until an author
         actually needs to add one.
       - **Verified byte-identical, not merely green.** The pre-refactor simulator was loaded
         from `HEAD` and its trade frames compared with `.equals()` across all six registered
         modes on real data (cot_neutral CLS/C, channel, atr_trail, barriers, willco_neutral) —
         all identical. A passing suite is not sufficient evidence for refactoring the simulator
         every downstream verdict is computed from.
       - Changing the default was safe *because it was checked*: every caller already passed an
         explicit mode.
       - **New evidence for this ADR.** `npf.strategies.__init__` imports `NpfStrategy` ->
         `cotmetrics`, so engine modules with no data dependency of their own inherit one from
         the package they sit in. `tests/test_exit_registry.py` has to skip when the store is
         absent for exactly that reason. The split removes it structurally: in the framework
         repo, `exits.py` imports nothing but numpy.
4. [x] Extract `RulesStrategy` + `STRATEGY_REGISTRY` and `config_parser` from the packages they
       currently share with implementations. **DONE 2026-07-22** (npf #106, `90bcf1d`) —
       `src/framework/{strategy,config,registry}.py`, 15 tests.
       - **The contents were never the problem.** `base.py` imported only stdlib and pandas,
         `config_parser.py` only yaml/typing/pydantic. The *package* was: `npf.strategies`
         `__init__` imports `NpfStrategy` -> `cotmetrics`, so the interface definitions
         inherited a data-store dependency they had none of their own.
       - **The registry ships empty and strategies register themselves** — the same shape as
         the exit rules. This is the piece that removes the inherited dependency; a registry
         with an implementation baked in drags that implementation's data store everywhere.
       - **Call sites deliberately not churned.** Old paths are thin re-export shims, because
         the ~35 call sites would otherwise change twice: once to `npf.framework` now, and
         again to `crucible_stack` at the move. They change once, at the move.
       - Verified in a *subprocess* that the framework imports with no data store and drags in
         no npf/app modules, so a stub loaded by another conftest cannot make it pass for the
         wrong reason. The empty registry is asserted on the syntax tree, not at runtime where
         an earlier import could have populated it.
5. [x] Move `portfolio_mc` and **only** `weighted_monthly_returns`; re-run the publishability
       audit at execution time rather than trusting the list above. **DONE 2026-07-22** —
       `src/framework/montecarlo.py`. The audit was re-run and changed the answer again:
       - **`run()` stayed behind.** `portfolio_mc` is not homogeneous: five functions are MC
         machinery, but `run()` PRINTS a drawdown/ruin table. Computation moved; the report did
         not, following the same split ADR-0002 drew for crucible ("core is pure computation;
         `report` does I/O"). A library that prints to stdout is the wrong thing to publish.
       - `weighted_monthly_returns` confirmed self-contained (ten lines, no internal deps) and
         confirmed the only symbol `npf.capital` takes from `sizing.py`.
       - All six moved functions verified **byte-identical** to `HEAD` by source comparison.
         The first run of that check reported a false difference, because the helper matched
         `block_bootstrap_paths` when looking for `block_bootstrap` — the verification was
         wrong, not the code. Anchor such matches on `def name(`.
       - Old paths re-export, so call sites still change once, at the move.
6. [x] De-npf-ify the framework's docstrings — `sweep.py`, `simulators.py`, `__main__.py` carry
       illustrative CMR references. Same treatment ADR-0002 required of `SearchSpaceLog`.
       **DONE 2026-07-22.** The pass found more than prose:
       - *** **`RulesStrategy` defaulted `exit_spec` to `'cot_neutral'`.** The same defect as
         the engine's old default, one level up — in the class an author subclasses. Both
         existing strategies override it, so nothing broke; a *new* strategy that set no exit
         would have silently inherited one reading COT columns its frame may not have. The base
         class now expresses no opinion (`{}`) and the engine's generic default applies. Two
         regression tests, one on the syntax tree.
       - The **"(private)"** labels on `optimize` / `capital` / `orchestrate` contradicted this
         ADR and are gone; `bands.py`'s "Stays in npf" claim was simply wrong.
       - The crontab example in `__main__.py` named `npf_books.cmr:build` — a module that does
         not exist, for a book that halts. Replaced with a neutral one carrying the `mkdir -p
         var` and `--quiet` fixes the runbook already had.
7. [x] Wire `npf/requirements.txt` and CI to the new repo, mirroring the existing crucible step.
       **DONE 2026-07-22**, together with the move itself — item 7 turned out to presuppose a
       repo that no action item created. `crucible-stack` now exists (private until item 10),
       holding `framework/`, `optimize/`, `capital/`, `orchestrate/` and `engine/` (npf's old
       `strategies/simulator.py` + `exits.py`; the rules engine is not a strategy, and
       `strategies/` would have been a misleading name in a repo containing none). npf imports
       it and vendors none of it. Verified before deleting: all 23 moved files byte-identical
       to their npf originals modulo the import rewrite.
       - *** **The move exposed a leak this ADR had not caught.** The framework's config
         schema required `macro_neutral_line` and `reversal_patterns` — one strategy's COT
         parameters, on `ExitLogic`/`EntryLogic` that **every** other strategy would have had
         to satisfy. The file list called `core/config_parser.py` "the params/config schema"
         and moved it whole. crucible-stack's own vocabulary guard caught it on the first run.
         Fixed with the registry pattern: the framework types `strategy_space` as
         `Optional[Any]` and takes `load_config(..., model=)`; npf subclasses and narrows it.
         Narrowing is load-bearing — npf reads `config.strategy_space.entry_logic` by
         attribute, and one consumer uses `getattr(..., default)`, so a plain dict would have
         degraded silently to a default probability rather than raising.
       - `crucible-stack` cannot be published yet for an unrelated reason: the honest-N API it
         depends on landed in crucible **after** the v0.2.0 tag, so `crucible>=0.2.0` would
         resolve to a version whose `deflated_sharpe` has no `n_trials`. Pinned to
         `crucible@main` as an explicitly temporary direct reference. **crucible needs a 0.3.0
         release**, and a direct reference blocks PyPI upload.
       - npf CI needs a `CRUCIBLE_STACK_TOKEN` secret while the repo is private. That line
         comes out when it goes public; nothing else changes.
8. [x] Relocate the design docs; split `architecture.md` between the two repos.
       **DONE 2026-07-22.** This ADR and ADR-0003 moved here with the code they describe,
       each gaining a provenance header: the bodies still use the pre-move module paths, and
       rewriting a decision record to match today would be falsifying it, so the header
       carries the mapping instead. `seam-contracts.md`, `toolchain.md` and `orchestrate.md`
       moved and *were* repointed, being living documents rather than history. npf keeps
       ADR-0001 (its backtest engine) and ADR-0002 (promoting `SearchSpaceLog` into
       crucible), which are npf's own decisions, plus a stub for each moved ADR.
       - `architecture.md` was split into two, and both halves were rewritten rather than
         cut in half: the npf one still described `optimize`, `capital` and `orchestrate` as
         npf packages and was stale by five. Its dependency tables are now **generated from
         the imports** rather than maintained by hand, which is what let the staleness sit
         there unnoticed.
       - `analysis_tools.md`, `schema_overview.md`, `docs/npf/` and `docs/archive/` stay in
         npf. The audit that decided this scored each doc for strategy-revealing vocabulary;
         the only hits in the moved set were module and parameter *names*
         (`cot_neutral`, `willco`, `comms_idx`), never index construction or a finding.
9. [x] Replace `tests/test_orchestrate_boundaries.py` with the framework repo's own guard: the
       framework must import **zero** npf modules, and carry no strategy vocabulary.
       **DONE 2026-07-22** as `crucible-stack/tests/test_boundaries.py`, and mutation-checked
       rather than trusted: a strategy import, an undeclared dependency, a strategy-named
       function, and each registry pre-populated were all confirmed to make it fail. The first
       registry mutation silently did not apply and reported a false pass, so the harness now
       asserts the mutation landed.
       - npf's `test_framework_boundary.py` is replaced by `tests/test_crucible_stack_seam.py`,
         which tests npf's half: that npf populates the empty registries, that the old paths
         resolve, and that npf's config narrows the framework's.
       - Worth recording: when `src/framework/` left, that file's parametrized scans collected
         **zero** cases and reported green. Only `test_there_is_something_to_check` failed,
         which is the whole reason that assertion exists. A guard that stops seeing its target
         passes forever.
10. [x] Write an **API stability policy** before the framework is published. Interfaces others
        write against cannot keep changing at will.
        **DONE 2026-07-22.** `docs/api-stability.md` is the promise; `tests/test_public_api.py`
        is the mechanism, and pins the exact surface. A policy in prose alone gets broken by a
        rename in an unrelated refactor and nobody notices until an install downstream fails,
        so the guard is mutation-checked: removing a name, adding an unpinned export, deleting
        a deprecated alias, and advertising a name that does not exist were each confirmed to
        make it fail.
        - *** **Writing the surface down found it was not defined.** `Reoptimization` — the
          return type a book adapter must construct — was **not exported at all**, so the
          orchestrator's own protocol could not be implemented without reaching into a
          submodule. Neither could `run_cycle`. And a downstream package had come to depend on
          `montecarlo._max_drawdown`, private by convention and public in fact. It is now
          `max_drawdown`, with the old name kept as a deprecated alias and npf migrated off it.
        - Five modules had **no `__all__`**, so their public surface was whatever happened to
          be importable. `framework.config`, `framework.montecarlo`, `framework.strategy`,
          `optimize.select` and `engine.simulator` now declare one.
        - The policy states the pre-1.0 position plainly rather than burying it: **breaking
          changes may land in a minor release while 0.x**, never silently, with deprecation
          where skipping it would cost anything. Registry *contents*, numerical output and
          exception messages are explicitly outside the promise.
        - A `CHANGELOG.md` now exists, which the policy depends on to make "never silent" true.
11. [x] Note the multi-editable-install hazard in both READMEs.
        **DONE 2026-07-22.** npf's README gains an *editable-install hazard* section under
        setup; crucible-stack's gains *If you install this editable, read this first*, aimed
        at anyone developing a strategy repo against it rather than at this workspace.
        - npf's setup section was also stale: it listed four editable links and there are now
          six packages installed that way, `crucible-stack` among them.
        - The four edges documented are the ones that actually bit during the extraction, not
          a generic warning: a git operation in a sibling changes npf with no npf change;
          deleting is global across every venv on that checkout; **`pip list` versions are not
          the code you are running** (the metadata says `crucible 0.2.0` while the tree holds
          post-tag API, which is exactly why `crucible>=0.2.0` looked satisfiable); and stale
          `__pycache__` outlives a `git rm`, so a deleted package still appears in `ls`.
        - crucible-stack's note adds the one that cost npf a red CI: **do not declare a
          dependency by URL to work around a stale release.** A direct reference cannot be
          reconciled with a consumer installing the same package from a local editable
          checkout, and it blocks PyPI upload.
12. [x] Confirm Proposed → Accepted before any file moves, and re-check the boundary evidence at
        execution time rather than assuming it from this document.
        **ACCEPTED 2026-07-22 — and the re-check earned its place.** Run fresh rather than read
        off this document, it found the toolchain still importing `npf.validation.portfolio_mc`
        and `npf.validation.sizing`: `capital/bands.py`, `orchestrate/drift.py` and
        `orchestrate/account_drift.py` reached the MC engine through npf's *compatibility shim*
        instead of its canonical home in `npf.framework.montecarlo`. Item 5 kept the shims
        deliberately so npf's ~35 application call sites would not churn — correct for code that
        **stays**, wrong for code that **travels**. Those three modules move to `crucible-stack`,
        so each would have carried an `npf.validation` import into the public repo: a
        framework→private-strategy-repo dependency, discovered at move time, in exactly the
        situation this ADR exists to prevent.
        - Fixed by repointing only the four travelling imports. npf's own call sites still use
          the shim, as item 5 intended. The distinction is *does this file move*, not *is this
          import tidy*.
        - `tests/test_orchestrate_boundaries.py` **failed on the fix**, since its allowlist named
          `npf.validation.portfolio_mc`. It was right to: the allowance was an artifact of the old
          layout. Removing it means reaching into `npf.validation` is a boundary violation again,
          which is the property item 9 will inherit.
        - Evidence at acceptance: toolchain → strategy/app `NONE`; toolchain → `npf.validation`
          `NONE`; framework imported standalone with no `COTDATA_STORE` leaks `NONE`.
          461 passed, 10 skipped.
        - **The lesson to carry into items 7-9:** every remaining item moves files. Re-run the
          evidence at each one. A boundary is a claim about the code as it is now, and this
          document was four days stale after two of its own action items.
