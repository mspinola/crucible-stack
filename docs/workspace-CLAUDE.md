# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in the
`trading_workspace` directory.

**Where this file lives:** the canonical copy is tracked here, at
`crucible-stack/docs/workspace-CLAUDE.md`; the workspace-root `CLAUDE.md` is a symlink to it.
Edit it here and commit, so every correction gets a diff. The narrative history behind these
rules (what this file used to claim, what each wrong claim cost, and how each correction was
established) is versioned beside it in
[workspace-history.md](workspace-history.md). When a rule below surprises you, the history file
says why it exists. Trimmed from the 911-line accreted version on 2026-08-29 (npf
`docs/handoffs/2026-08-29-claude-md-trim-v2.md` is the work order).

## What this directory is

`trading_workspace` is **not a git repo**. It is a container for sibling checkouts, each its own
GitHub repo under `mspinola/`. The sibling layout is load-bearing: editable installs, CI checkout
steps, and `requirements.txt` entries all use relative `../<repo>` paths.

| Directory | Visibility | Role |
|---|---|---|
| `cotdata` | public, PyPI | data layer. Producer writes Parquet to a file store, consumers only read. Four CFTC reports since 2026-08-03: Legacy, Disaggregated, TFF, Supplemental |
| `marketdata` | public | daily bars. Equity/ETF from yfinance (corporate-action adjustment derived on read, never stored); futures from Norgate since 2026-08-09. Created 2026-07-26 |
| `cotmetrics` | public, PyPI | COT positioning index, signals, `CotIndexer`. Reads the cotdata store |
| `cot-analyzer` | public, source app | Dash/Plotly UI over cotmetrics. Computes no metrics of its own |
| `crucible` | public, PyPI | capital-free edge validation. The gate/judge, and since 0.6.0 the post-promotion decay monitor |
| `crucible-stack` | public, PyPI | define / search / size / deploy framework. Ships **zero** strategies |
| `npf` | private | the strategy repo (Pardo Quant Framework). Rules + ML books, the CMR/PF positioning work |
| `livebook` | private | forward paper trading of a promoted book, ledger + drift monitor |
| `crowdmon` | public, **DEPRECATED 2026-08-07** | crowding & forced-exit monitor, frozen not deleted. Read `crowdmon/DEPRECATED.md` first |
| `cotmetrics-config` | private | the real `params.yaml` (instrument universe, tuned lookbacks) |
| `bak-*` | local only | old checkouts, not live. Do not edit |

Dependency direction is one way: `cotdata` <- `cotmetrics` <- `cot-analyzer`, and
`crucible` <- `crucible-stack` <- `npf` <- `livebook`. crucible-stack never imports strategy code,
enforced by `crucible-stack/tests/test_boundaries.py`.

`crowdmon` (deprecated) is the only package that joins cotdata and marketdata, which is why it is
its own sibling rather than code inside either producer: normalisation is a joiner by definition,
and hosting it in either would drag the other's data across the seam ADR-0007 draws. It imports
neither `cotmetrics` nor `crucible` (a monitor that can import the judge can render a verdict on
its own output); all directions enforced by `crowdmon/tests/test_boundaries.py`.

`marketdata` is a **sibling of** `cotdata`, not a consumer: it imports nothing from it. The two
agree on symbol naming by convention. It exists because `cotdata.registry` hard-requires a
`cftc_code` and equities have no COT report.

`cotmetrics` prices MFS and MME off `EFA` and `EEM` (ICE MSCI futures with no Norgate continuous
series) via `cotmetrics.market_data.PRICE_PROXIES`, which is deliberately NOT
`options_data.ETF_PROXIES`: that one maps ES to SPY for illiquid options chains, and reusing it
for prices would swap S&P futures for an ETF across the whole book.

### The month-end pin

Month-end studies read `~/code/marketdata_store_frozen_2026-07-24` (frozen copy, nine equity
symbols, no futures, deliberately OUTSIDE `marketdata_store` so a whole-store mirror cannot reach
it). Verify with:

```bash
MARKETDATA_STORE=~/code/marketdata_store_frozen_2026-07-24 \
  marketdata-update --verify-pin npf/docs/robot_wealth/month_end_snapshot.json
```

Drift against the LIVE store on all six pinned symbols is the **expected** answer, not a fault: a
scheduled task refreshes the live equities half nightly by design. Do not re-pin the live store
and do not hand-restore the six into it. Only the frozen copy reproduces the published figures,
because Yahoo has since dropped four bars from its own history, so the data cannot be re-fetched.
`npf/src/npf/books/month_end.py::load_pair` verifies the configured store against the snapshot and
raises, naming what drifted and which store to use (it deliberately does not rewrite the
environment; cron and a shell must not disagree). Second independent frozen source:
`~/code/cot-backup/marketdata_store`.

The FUTURES half is live: written nightly by the Windows producer, read by `cotmetrics` and
`cot-analyzer`. Nothing pins it; the check after touching it is `marketdata-update --check` plus
whether `last_date` advanced.

### Two standing mirror rules (not discoverable from code)

- **Every equity symbol must exist on the Windows store.** The nightly `robocopy /MIR` purges
  whatever the source lacks, so a symbol seeded on the Mac alone is a delayed-action delete (this
  has fired once). Seed on Windows and let the 17:30 producer deliver it; the code skips rather
  than fails on missing bars, so the symptom is empty price columns, not a crash.
- **Keep the `--domain` flags on the two Windows bars tasks.** The two halves run on different
  tasks at different times, so a bare `--bars` would duplicate the other's work, put yfinance
  fetches behind an unrelated futures finals gate, and race the two wrappers' mirror passes. The
  full rationale lives in the wrapper headers (`run-prices.cmd`, `run-equities.cmd`).

### The CBOE implied-vol indices

The four (GVZ, OVX, VIX, VXN) are registered and consumed by `npf/trading_riot`
(`vrp_multi.py:36` maps each to a realized counterpart; without them the study's GENERAL gate
cannot run). `npf` is private, so **a clean grep of the public `marketdata` repo cannot see the
dependency and is not evidence the symbols are unused**; they have been deleted twice on exactly
that reading. `marketdata/src/marketdata/registry.yaml` carries the reason and the exit condition
inline beside the symbols and is the authority. Check the registry before acting on anything
written here.

### ADR-0007 is done in full

[ADR-0007](adr/ADR-0007-cotdata-is-cot-only-bars-live-in-marketdata.md) (accepted 2026-07-27,
complete 2026-08-09) moved ALL bars plus the contract-specs table out of `cotdata`. cotdata is
CFTC positioning only, with **no price code of any kind**: no Norgate, no databento, no
`--prices` / `--metadata` / `--require-final` flags, no `cotdata-prices` entry point. Trap: an
editable install does not delete console scripts it stopped declaring, so a stale
`.venv/bin/cotdata-prices` can survive on PATH; it raises `ImportError: cannot import name
'main_prices'` and exits 1, which looks like a broken install rather than a removed command.
`cotdata-cot` survives as an alias of `cotdata-update` because scheduled jobs call it by name. A
store that cannot serve the universe fails at boot via `marketdata.coverage_gaps` and
`cot-analyzer`'s `check_price_store`.

- **Contract specs**: only marketdata's `metadata/contract_specs.parquet` is refreshed
  (`marketdata-update --metadata`); cotdata's copy is frozen forever. npf's `validation/costs.py`
  lists marketdata FIRST in `SPECS_SOURCES` and keeps the dead cotdata entry as a tripwire
  (rationale commented there): anything that makes it answer again has restored a table nothing
  refreshes.
- **Databento** lives in `marketdata/providers/databento.py`: the cross-platform producer
  (`--ingest-databento` paid, `--build-databento` free and offline) that lets a non-Windows box
  serve futures, since NDU is Windows-only. Not a Norgate parity vendor: history starts at the
  GLBX floor of 2010-06-06 and eight registry markets are not on CME Globex.
- Both stored futures vendors are **continuous only** (Norgate `_CCB`, databento `.n.0`/`.n.1`).
  There is no per-expiry series in the store, and that is a **code gap, not a data gap**:
  `marketdata/providers/norgate.py::_reconstruct_volume` fetches full per-contract OHLCV on every
  futures run and keeps only Date/Volume/Symbol, and the stored frames name the front contracts
  per bar. Calendar-spread, same-delivery-month, or trade-the-actual-contract studies are blocked
  on a schema decision, not on the subscription. See `marketdata/docs/design.md` (Known holes)
  and `npf/docs/robot_wealth/futures_roll.md`.
- **Point-in-time reads**: `get_bars(sym, tier, asof=...)`, futures only (since 2026-08-24).
  Returns the exact vintage current at the as-of date; it is NOT `end=`, which truncates today's
  restated series. The distinction bites RATIO-based logic hard (~25% of ROC / mean-relative days
  differ on HE against an old vintage) and crossovers/breakouts on zero days, because a constant
  cancels out of a comparison and not out of a ratio. Equities raise rather than ignore the
  argument (their vintage needs a different derivation).
- Adding CFTC report types is in scope even when new: copy the pattern of
  `cotdata/docs/adr/ADR-0002-supplemental-report-is-in-scope.md` (applies the narrowed boundary's
  own rule, lives in cotdata).

## Governance: read AGENTS.md first

`.agents/AGENTS.md` at the root is a pointer only. The canonical, version-controlled rules live
in `npf/AGENTS.md` (research governance, applies everywhere) and `crucible/AGENTS.md` (working on
or with the library). Read them before doing research work. The core rule:

> **You are the intern, crucible is the judge, the human decides.**

Consequences that bite in practice:

- Never render the verdict on a book you authored in the same session. Generator pass and
  evaluator pass stay separate.
- Every variant tried, including discards, goes into a `crucible.validation.SearchSpaceLog` and
  its count feeds `run_gauntlet(..., n_variants=log)`. An undercounted denominator is a broken
  gate.
- Build trade logs with `holdout` / `walk_forward` so purge and embargo hold by construction.
  Assume look-ahead until proven otherwise.
- Pre-registered specs (`livebook/docs/PREREGISTRATION.md`,
  `npf/docs/trend_following/trend_following_cot.md`) are binding. Drift from a frozen spec is a
  finding to surface, not a detail to reconcile.
- Every quoted figure carries a reproducer (script, seed, data reference).
- Working session markdown (plans, notes, findings) is authored under the relevant repo's `docs/`
  from the first keystroke, never drafted in the agent scratchpad.
- No em dashes in any output, chat or committed prose. Use a comma, period, or parentheses.
- Any response reporting results, findings, or a verdict ends with a plain-language recap of the
  bottom line (strength in words: genuine null, marginal lean, significant), alongside the
  technical answer, not replacing it. Canonical: npf/AGENTS.md governance #8 and #9.

## Architecture: the chain of refusals

Each layer answers one question and refuses the next one. Respect the seams, and do not let one
session generate a hypothesis, run its own gate, and narrate the result as a win. Full write-up:
[toolchain.md](toolchain.md).

| Layer | Asks | Refuses to ask |
|---|---|---|
| `crucible_stack.optimize` | which config, and what did the search cost? | is it real? |
| `crucible` | is the edge real, corrected for the search? | what would it earn? |
| `crucible_stack.capital` | what does an account trading it look like? | should we deploy it? |
| `crucible_stack.orchestrate` | is it still right, may it go live? | n/a |
| `crucible.validation.monitor` | has a PROMOTED edge decayed? | should we cut size? |

The last row runs *after* promotion, so it is a peer of the gauntlet rather than a fifth gate;
nothing wires it into `run_gauntlet`. It keeps every crucible invariant (no clock, no
persistence, capital-free) and refuses to convert its verdict into a sizing action.

```
data -> signal -> optimizer --TrialMatrix--> crucible --TradeLog--> capital sim --EquityResult--> orchestrator
   ^                                                                                                  |
   +------------------------- DeploymentLedger.current(book) -----------------------------------------+
```

`TradeLog` is the pivot: capital-free, denominated in R. That is what lets crucible judge without
knowing account size. The 1R-to-currency conversion is pinned to one place
(`EquityResult.meta["r_denominator"]`). Contracts, not implementations, are the invariant
([seam-contracts.md](design/seam-contracts.md)).

crucible's public function names are the contract users pin against, declared in `__all__`
(crucible-stack pins its own surface in `tests/test_public_api.py`). Do not rename, move, or drop
a public symbol without a deprecation path, do not add a flag that softens a hard check into a
warning, and keep crucible capital-free. Determinism is a feature: randomized procedures take an
explicit seed and must reproduce.

## The edge monitor (spans three repos)

Answers "is the promoted edge still delivering?", which `orchestrate.drift` does NOT: drift
watches the equity **path** (cumulative R and drawdown against a frozen block-bootstrap
envelope), the monitor watches the per-trade **parameter** (expectancy, firing rate). They come
apart in both directions, so run both.

| Repo | Owns |
|---|---|
| `crucible` | the judging. `validation.monitor`: `EdgeBaseline`, `cusum_design`, `edge_monitor` -> HOLDING / SLIPPING / DEGRADED, `rolling_expectancy`, `cusum_path`, `empirical_arl`. Plus `validation.deflated_expectancy` and `report.monitor_panel` |
| `crucible-stack` | the state. `orchestrate.decay` + `EdgeDecayTrigger`: freeze an `EdgeBaseline` at promotion, persist it in the `DeploymentLedger` beside the `DriftEnvelope`, read it back, turn a verdict into a re-optimization trigger |
| `npf` | the data. `books.trend` exposes `trade_r_since` / `trade_dates_since` and freezes a baseline in `reoptimize` |

Rules:

- **Enable `--edge-decay` only AFTER a promotion has written a baseline to the ledger, never
  before.** `EdgeDecayTrigger` fails open, so an early enable fires and re-optimizes every cycle,
  and every re-optimization adds variants to the `SearchSpaceLog`, inflating the multiple-testing
  denominator of the very verdict the trigger exists to refresh. (`npf/scripts/orchestrate_trend.sh`
  has passed it since 2026-08-02, enabled in that order; its inline comments cover why
  `--arl0-years 10` rather than crucible's default 25.)
- **Only the calibrated CUSUM may say DEGRADED**; the rolling-window and firing-rate ratios cap
  at SLIPPING. Measured reason: on a healthy book sitting a quarter ABOVE baseline, the 200-trade
  trailing read still swung between -31% and 240% of baseline, so a "cut at 50%" rule would have
  cut a healthy book.
- The false-alarm budget is denominated in **calendar time** (`Thresholds.monitor_arl0_years`,
  converted via the baseline's firing rate), and **skew** inflates ARL0 (about 2x at skew +5 on a
  real pooled book) while detection latency tracks nominal. Both measured against the real
  47-market universe after a fully green suite missed them; detail in
  `crucible/docs/edge_monitor.md`.
- The monitor has **never been run against a book that genuinely decayed**. Latency figures are
  design targets, not field results.

## Environments

Venvs are per-repo, but **two do the work**:

- `npf/.venv` (Python 3.11) is the master environment: `npf`, `cotdata`, `cotmetrics`,
  `crucible`, `crucible-stack` and `marketdata` as editable installs. `livebook` and `cotmetrics`
  tests run here too (`livebook/bin/daily.sh` defaults `VENV_PY` to it).
- `cot-analyzer/.venv` holds the Dash stack plus editable `cotdata` and `cotmetrics`.

`crucible/.venv`, `crucible-stack/.venv`, `cotdata/.venv`, and `marketdata/.venv` exist for
isolated library work.

**Phantom-package hazard**: any sibling NOT installed in a given venv imports from the workspace
root as a bare namespace package rather than failing (`__file__` is None, `__path__` points at
the checkout). Check `__file__`, not merely that the import succeeded.

```bash
cd npf && uv venv --python 3.11 && source .venv/bin/activate && uv pip install -r requirements.txt
```

### The editable-install hazards

The installed package *is* the working tree. There is no copy.

1. A `git checkout` / `stash` / `rebase` in a sibling changes npf's behaviour with no npf change.
   A test that passes then fails against an untouched tree is usually this. Check
   `git -C ../crucible-stack status` before hunting a flake. Other Claude sessions share these
   checkouts.
2. `rm` in a sibling removes it for every venv pointing at it.
3. `pip list` versions are install-time metadata and are never refreshed, so a version pin is not
   evidence of an API. Check the signature. Refresh with
   `python -m pip install --no-deps -e ../crucible`.
4. Stale `__pycache__` outlives a deleted module. The real package is `npf/src/npf/`; pre-layout
   leftovers under `npf/src/` are pycache-only. Clean with
   `find src -name __pycache__ -exec rm -rf {} +`.
5. **A running test suite may not be testing the tree you edited.** In a worktree
   (`.claude/worktrees/<name>`) the editable install still resolves siblings to their MAIN
   checkouts, so the suite passes against code you did not change. Verify with
   `python -c "import cotdata; print(cotdata.__file__)"` rather than trusting a green run, and
   shadow with `PYTHONPATH=<worktree>/src`.

**Tagged is not published.** Check `git tag -l` against PyPI before reasoning about external
consumers, and a dated CHANGELOG heading is not evidence of a release either (crucible once went
0.3.1 -> 0.5.0 on PyPI with a fully formatted, dated, never-tagged 0.4.0 section in between).
PyPI carries cotdata 0.1.0 and 0.3.0: everything through 0.3.0, which is the whole producer CLI
and the vintage subsystem, IS published, so deleting from it is a breaking change. `v0.2.0` is
tagged-but-never-uploaded.

Keep `setuptools<81` in both working venvs or `pkg_resources` breaks.

### Environment variables

| Var | Meaning |
|---|---|
| `COTDATA_STORE` | the shared store, `~/code/cotdata_store`. Required by nearly everything |
| `COTMETRICS_PARAMS` | real universe config. Unset means a 6-symbol **sample**, silently |
| `COTMETRICS_CACHE` | derived per-instrument parquet cache |
| `COT_VIZ_CONFIG` | cot-analyzer's viz config path |
| `CMRDATA_STORE` | npf's output/state store, `~/code/cmrdata_store`: the weekly setup lists and the append-only `deployments.jsonl`. Deliberately separate from `COTDATA_STORE`; both entry points raise if unset |
| `MARKETDATA_STORE` | marketdata's store root, `~/code/marketdata_store`. Separate from `COTDATA_STORE`, and must not share its `manifest.json`: `_touch_manifest` is a read-modify-write, so two producers on one manifest lose entries |
| `MARKETDATA_PRICE_SOURCE` | marketdata's deployment default vendor, `yfinance` if unset |
| `MARKETDATA_NO_NETWORK` | skip marketdata's network-marked tests |

The store vars are exported from `~/.zshrc` and `~/.bash_profile`, but **a scheduled job's
environment is a property of its launcher, not of the shell you test it from**: launchd reads no
profile, and both stores raise on an unset var. Every launcher therefore defaults what it needs:
`livebook/bin/daily.sh` defaults `COTDATA_STORE`, `COTMETRICS_PARAMS`, `COTMETRICS_CACHE` and
`PYTHONPATH` (the 07:30 job); `npf/scripts/weekly_setups.sh` and `orchestrate_trend.sh` default
those plus `CMRDATA_STORE` and `MARKETDATA_STORE` (the Friday 17:00 and 18:00 jobs). When a read
moves into a new package, chase the new variable into **every launcher that will make that
read**; a launcher that misses it fails loudly in a log nobody reads and invisibly everywhere
else. On Windows it is set twice over: persistently via `setx` and inline in `run-prices.cmd`.

`COTMETRICS_PARAMS` falling back to the sample is the most common silent-wrong-result failure: a
run scans a fraction of the book and reports as if it scanned all of it. The launchers
(`npf/run-local.sh`, `cot-analyzer/run-local.sh`, `livebook/bin/daily.sh`) set it for you, which
is why you use them rather than calling `python main.py` directly.

## Commands

### Tests and lint

Every repo runs `pytest` the same way, but **`ruff` scopes differ per repo** (table below). All
of them need a store env var set, even for store-free tests (cotdata guards at import), and
**npf needs `MARKETDATA_STORE` too**: the book adapters read positioning and prices from two
different stores. A miss is 26 skips that name the absent variable (both `needs_data` guards key
on both stores since npf #207). Set it anyway: skipped is not tested. The general rule: **when a
read moves to a new package, grep for every launcher, guard and skipif that mentions the OLD
store**; none of them fail at the seam, they fail somewhere downstream that looks unrelated.

Do not point tests at `~/code/marketdata_store`: that runs the suite against the live store
rather than the fixture (a different test, 542 rather than 525) and reads the pinned month-end
snapshot. Project the fixture instead, exactly as CI does:

```bash
cd npf && COTDATA_STORE=$PWD/tests/fixtures/store .venv/bin/python tests/fixtures/make_marketdata_store.py /tmp/md_store
```

```bash
cd npf && COTDATA_STORE=$PWD/tests/fixtures/store MARKETDATA_STORE=/tmp/md_store COTMETRICS_PARAMS=$PWD/tests/fixtures/params.yaml COTMETRICS_CACHE=/tmp/cm_cache .venv/bin/python -m pytest tests/ -q -rs
```

The marketdata fixture is **projected from the committed cotdata fixture** rather than committed
twice, so the two cannot drift. Build it once per checkout.

```bash
cd crucible && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check src tests examples
```

**Lint scope is per repo.** Copy the scope from the row, not from the repo next door: linting
narrower than CI passes locally and fails in CI.

| Repo | `ruff check ...` |
|---|---|
| npf, cotmetrics, cot-analyzer, marketdata | `src tests` |
| crucible | `src tests examples` |
| crucible-stack | `crucible_stack tests` (flat layout, no `src/`) |
| cotdata | `src tests scripts` |
| livebook | `. --exclude build,dist` |

The row that bites: **cotdata lints `scripts/` while npf does not**, so `npf/scripts/` carries
ruff errors indefinitely (264 at last count) without CI objecting. Do not read a green npf run as
evidence that `npf/scripts/` is clean.

Six repos pin `ruff==0.15.22` in their `dev` extra; `crucible-stack` and `livebook` float on
`>=0.1.0`, so their CI resolves the newest ruff and a green local run can fail CI on a new rule.
**Verify with `python -m ruff --version`, never with the pin** (an installed version has drifted
from the pin before). `cotmetrics` and `livebook` have no venv by design; lint them from
`npf/.venv` with their own CI scope, run from the repo directory so ruff picks up that repo's
`pyproject.toml`:

```bash
cd livebook && ../npf/.venv/bin/python -m ruff check . --exclude build,dist
```

Every repo venv except `npf/.venv` is uv-created **without `pip`**, so `python -m pip install`
fails there. Use uv:

```bash
cd <repo> && uv pip install --python .venv/bin/python "ruff==0.15.22"
```

A single test, same env (the book adapters need it even when you name one test):

```bash
cd npf && COTDATA_STORE=$PWD/tests/fixtures/store MARKETDATA_STORE=/tmp/md_store COTMETRICS_PARAMS=$PWD/tests/fixtures/params.yaml .venv/bin/python -m pytest tests/test_books_trend.py::test_name -q
```

npf CI runs against a two-market fixture store (6A, GC). Tests carrying `needs_full_universe`
skip whenever the store holds fewer than 20 markets, which is always true in CI, so `-rs`
matters: a green CI run does not mean the pooled findings ran. The real store holds 51 markets,
of which `cotmetrics-config/params.yaml` names 47 across 9 asset classes.

**Five of the 47 are `heldout` (EMD, NKD, MME, MFS, KE), and only three are usable.** MME and
MFS are not in `marketdata/registry.yaml` at all: their COT is on ICE MSCI futures with no
Norgate continuous series, they are priced only through the cotmetrics ETF proxy, and nothing
under `npf/src/` imports that proxy, so an npf book asking for their bars gets nothing
(`config/npf/cmr_heldout_msci.yaml` yields no trades, and must be evaluated GROSS since the
proxy has no futures cost specs). The usable held-out set is **KE, EMD and NKD**
(`config/npf/cmr_heldout.yaml`), two of three being equity index futures: a hypothesis whose
rival explanation is equity drift can only be sign-checked on it, not discriminated. Verified
2026-08-25 by parsing both files.

Repos with internal dependency floors (`npf`, `cotmetrics`, `cot-analyzer`, `livebook`) also run
`python scripts/check_dep_floors.py` in CI. Run it after changing a sibling's version or a pin.

### npf analysis runs

`./analyze <mode> [config] [extra flags]` is the dispatcher over `./npf` (which is `main.py` with
the project venv). `./gauntlet.sh` and `./holdout.sh` are shims to it.

```bash
cd npf && ./analyze gauntlet                       # four-pillar REAL/STRONG/DURABLE/GENERAL verdict
```

Modes: `gauntlet`, `holdout`, `fullrange`, `classwf`, `wfa` (needs `--symbol`), `stage6`, `mc`.
Default config is `config/npf/cmr_cs_oinorm_liquid.yaml`. Output lands in
`results/tearsheets/<mode>_<config>.html`. Pointing `--config` at a directory compares every
config in it into a dated `results/` folder.

### cot-analyzer

```bash
cd cot-analyzer && ./run-local.sh                  # http://127.0.0.1:5001
```

No hot reload. Restart after every edit. Also wired as a preview config in `.claude/launch.json`
(`cot-analyzer`, and `crucible-docs` for the MkDocs site on :8000).

### livebook

The book is live, running weekdays 07:30 under `com.mspinola.livebook-daily`. Operations,
including what to do when a run refuses, are in `livebook/docs/OPERATIONS.md`.

```bash
cd livebook && ./bin/daily.sh --dry-run            # what would be recorded, writes nothing
```

The ledger is append-only and authoritative. Norgate back-adjusted series restate history on
every roll, so the daily recompute only *detects* events. A recompute that restates a logged
decision is an anomaly to surface, never to absorb.

### The Windows producer, and what runs where

One Windows box (`C:\Users\matt\code\`, hostname `NUCBOX_M8`) produces everything the Mac only
reads. Reachable at `\\NUCBOX_M8\Users\matt\code`, usually mounted at `/Volumes/code`, which is
enough to READ every wrapper and doc, not to inspect the tasks (`Get-ScheduledTask` runs on the
box).

**`scheduler\verify-scheduling.ps1` is the authority on this section, not this file.** Read-only
(starts nothing, writes nothing); it encodes the expected args and trigger shape per task, checks
store freshness and replica parity, and prints a GUARD PROOFS list of what it cannot check. Run
it on the box after changing anything here or after a suspicious night. Last run 2026-08-22: 30
pass, 0 warn, 0 fail.

| Task | Fires | Runs | Writes |
|---|---|---|---|
| `cotdata prices` | daily 20:55, repeats PT15M for PT5H | `scheduler\run-prices.cmd` | `MARKETDATA_STORE` (futures bars + specs), then both syncs |
| `marketdata equities` | **weekdays** 17:30 ET, no repetition | `scheduler\run-equities.cmd` | `MARKETDATA_STORE` (equities bars), then both syncs |
| `cotdata COT (catch-up)` | daily 08:10 | `scheduler\run-cot.cmd` | `COTDATA_STORE`, then both syncs unconditionally |
| `cotdata COT (Fri release)` | Fri 15:25, repeats PT2M for PT45M | `scheduler\run-cot.cmd --poll` | `COTDATA_STORE`, syncs only if new data landed |
| `cotdata vintage` | daily 17:00 | `scheduler\run-vintage.cmd` | `COTDATA_STORE` |

Task names are historical (renaming means delete-and-recreate); `cotdata prices` actually runs
`marketdata-update --bars --domain futures --require-final` then `--metadata`.

- **Judge a nightly run by the store, never by Last Result.** `--require-final` (the only finals
  gate in the stack) asks the data, not the clock: ready when Norgate holds a newer settled bar
  than the store across an `ES`/`CL`/`ZC` quorum, else defer with a non-zero exit. A healthy
  night ENDS on a defer: the final repeat finds the store already current and returns 1, hours
  after the capture succeeded. A deferred run and a failed run both exit non-zero, by design.
  `--final-cutoff` is accepted and ignored.
- **Retry is a REPEATING TRIGGER, never "restart on failure".** Task Scheduler's restart setting
  covers the engine failing to LAUNCH the action; a non-zero exit from the action is logged as
  success (event 102) and nothing is rescheduled. The real mechanism is `schtasks /RI 15
  /DU 0005:00`, each repeat being the cheap gate check. `verify-scheduling.ps1` FAILS a polling
  task with no repetition and WARNS on any `RestartCount > 0`. Where a trigger cannot express the
  retry, the loop lives inside the `.cmd`: `run-equities.cmd` does 3 attempts 5 min apart, using
  `powershell Start-Sleep` because `timeout /t` fails under a scheduled task.
- **`marketdata equities` is weekdays-only**, so the equities half does not move over a weekend
  and a Saturday reading of it is Friday's. Its wrapper header's "this is daily" comment is about
  cadence (a bad capture self-heals next run), not the trigger. The design rationale (separate
  task rather than a step in `run-prices.cmd`; symbol fetch deliberately unscoped so
  `registry.yaml` additions get fetched; in-cmd retry) is in `run-equities.cmd`'s header.
- **Delivery**: two scripts, `sync-store.cmd` (robocopy `/MIR` to the Mac, two passes with
  different exclusions) and `push-to-server.cmd` (rsync to the VPS); **each mirrors BOTH
  stores**, and three wrappers call the pair, so whichever producer ran last carries the other
  half and neither replica sits stale. The 08:10 catch-up syncs **unconditionally on purpose**
  (`run-cot.cmd`'s header says not to "simplify" it: COT is weekly, and guarding the catch-up
  skips the sync on exactly the days the safety net exists for). The Friday poller guards on
  `status.json`'s `newest_data` (not a file hash or timestamp; measured reasons in the header)
  and fails toward syncing.
- **`sync-store.cmd` exclusions**: `/XF` matches by name at ANY depth. The COT pass excludes
  `manifest.json`, safe only because cotdata's live bookkeeping is under `manifests/` and
  `vintage\snapshots.json`; the bars pass must NOT copy that exclusion, because there
  `manifest.json` is the only index and excluding it delivers parquet the replica cannot
  enumerate. Both traps documented in the script.
- Four `.cmd` traps, all already handled in the wrappers (the note is about not UNDOING them):
  NDU lives in the interactive desktop session, so `cotdata prices` keeps **"Run only when user
  is logged on"** (verify-scheduling enforces it, for that task only); a `.cmd` reports only its
  LAST command's exit code, so each step captures `ERRORLEVEL` on its own following line
  (`|| exit /b %ERRORLEVEL%` does NOT work: cmd expands it at parse time, returning the previous
  command's code); defer exits non-zero by design; angle brackets are unusable anywhere in a
  `.cmd`, comments included (cmd reads them as redirection).

### cotdata store

```bash
cotdata-update --cot-all                           # free CFTC download, any OS, all four reports
```

Store reads are local and never hit the network. Four CFTC reports, and the fourth is not on the
same basis as the other three. `--cot-supplemental` (Commodity Index Trader, 13 agricultural
markets, the only public source separating index flow):

- It is **futures-and-options COMBINED** where Legacy, Disaggregated and TFF are futures-only,
  so its `Open_Interest_All` is a different quantity for the same market and week. Nothing in
  the file says so; cotdata asserts the flag (established by matching OI against both Legacy
  series, 390/390 and 0/390). Do not difference or ratio across reports without accounting for
  it.
- Its Index Traders does NOT nest inside Disaggregated's Swap Dealer: the taxonomy is Legacy,
  carved out of commercial, non-commercial AND non-commercial spreading. Anything relating the
  two reports is an inference across differently-partitioned classifications.
- Coverage is **12 markets, then 13 from 2013**, with six renamed along the way. Derive the
  covered set with `cotdata-vintage coverage`; treating it as constant misreports the universe.

Measurement detail, including why the OI identity is exact on only ~55% of market-weeks (combined
reports round delta-weighted option equivalents; never worse than 2 contracts):
`cotdata/docs/analysis/2026-08-03-cit-supplemental-measurements.md`.

## Naming

The systematized results are never called "CMR"; the model is Positioning Fade (PF) / Raw PF /
NPF, and "CMR" refers only to the original discretionary thesis. Canonical: npf/AGENTS.md
governance #10.

## crowdmon (DEPRECATED 2026-08-07)

Development stopped; the package is frozen, not deleted. **Read `crowdmon/DEPRECATED.md` before
doing anything with it**: it is the authoritative record (the evidence, what freezing means, §4's
reopening conditions, §2.1 on the drifting live-pin test failures and why the fix is to
neutralise the pins, §3 confirming nothing is scheduled and nothing consumes it). The thesis
(`damage = crowding x illiquidity x holder fragility`) was tested four times against frozen
pre-registrations, each executed in `npf` because crowdmon's boundary tests refuse a `crucible`
import, and produced no positive result:

| test | result |
|---|---|
| §10 validation, the core claim | **`uninformative`**, and the clean episodes are **spent** |
| index share | genuine null, premise retired |
| fragility orthogonality | Stage 1 independent, **Stage 2 genuine null** |
| forced-flow mechanism | `supported` on the letter, **mostly artifact** |

The decisive point is that the core test is **finished**: §10 needs hand-identified clean
episodes, they are used up, and only new crises restore them. Do not file new work against the
composite; `crowdmon/docs/handoffs/README.md` is closed.

**The measurements outlived the thesis.** `crowdmon/docs/HARVEST.md` classifies all 108 numbered
findings as PORTED, RESOLVED or DIES; facts were restated, never moved, so citations into
`crowdmon/docs/` still land. Where the live facts went:

| now lives in | what |
|---|---|
| `cotdata/docs/design/cross-report-comparability.md` | Legacy and TFF agree on exactly two quantities; cross-report subtraction is not interpretable; `canonicalize_legacy` sets `spread_contracts` to NA so summing it prints a fake zero |
| `cotdata/docs/design/reading-the-store.md` | what a coverage ratio means, `volume="reconstructed"` vs `front`, holes vs migrations, databento cannot produce `propadj` |
| `cotmetrics/docs/positioning-series-properties.md` | exceedances arrive in episodes (effective sample ~ a fifth of nominal); correlating positioning LEVELS is spurious (near unit-root, median lag-1 0.956) |

The verdicts stay in `npf/docs/crowdmon/`, each with a reproducer and a pinned store manifest.
One open question handed to a live package: `cotmetrics` computes six price-against-positioning
**level** correlations per lookback whose null has never been measured; the 52-week columns are
the most trusted and the most at risk. Written up there as a check to run.

### What to carry forward

- **Measure, do not assume.** Probing the actual files overturned a written assumption in nearly
  every session there, including several of the package's own.
- **Specs are amendable.** If a measurement contradicts a doc, fix the doc in the same change and
  say so. Where the doc lives in a sibling checkout, record it in the consumer's
  `docs/design/amendments-*.md` rather than editing a shared working tree, and say that too.
- **Doc lifecycle**: `design/` is living, `handoffs/` is append-only and status-tracked,
  `analysis/` is point-in-time and never amended, `adr/` is immutable once accepted.
