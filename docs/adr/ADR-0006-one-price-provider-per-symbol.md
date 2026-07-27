# ADR-0006: One price provider owns each symbol end-to-end

> **Scope.** This is a `cotdata` producer-architecture decision. It is recorded in the
> toolchain ADR log (alongside ADR-0003/0004) because it sets the provider story the whole
> stack reads through, but every code change lands in `cotdata` and two of its consumers
> (`cotmetrics`, `cot-analyzer`). It changes **no** consumer read contract.

**Status:** Accepted (2026-07-27). Architecture implemented and validated (see Outcome). The
server's deployment landed differently than the original motivation: it keeps the Norgate
sync, and databento is a validated alternative provider rather than the server's source.
**Date:** 2026-07-23 (accepted 2026-07-27)
**Deciders:** Matt (sole maintainer)

## Context

`cot-analyzer` publishes a public web dashboard from an Ubuntu server. Futures prices come
from Norgate, which runs **only on Windows** (the Norgate Data Updater plus the `norgatedata`
package). Today prices reach the server by rsyncing the Norgate-produced `cotdata` store from a
Windows box. That rsync-from-Windows link is the fragile part we want to be able to drop for
the server, while **local research stays on Norgate** and keeps its deep history.

The goal: let the server-side dashboard source prices from **databento** (or yfinance for
markets neither carries), removing the hard Windows dependency, without changing the meaning of
any number the rest of the stack computes.

Four facts found while scoping this (they shrink the problem a lot):

1. **Consumers are already vendor-agnostic.** Nothing in `cotmetrics`/`cot-analyzer` calls a
   price vendor. Every live read goes through `cotdata.get_prices(symbol, adjustment=...)`,
   which reads Parquet from the store (`cotmetrics/market_data.py`, `signals.py:1028`,
   `CotIndexer.py:836`). The store is already the provider boundary.
2. **Everything reads `backadj`.** No consumer reads `unadj` or `propadj`. The plotted price
   *and* the price-derived metrics (Spearman-vs-price, MACD, trend regime, velocity) run on the
   back-adjusted continuous series.
3. **Positioning Open Interest comes from the CFTC report, not the price provider.** The %OI,
   net-normalized, WILLCO and OI-zscore metrics all divide by `OPEN_INTEREST_XLS`, the OI column
   from the weekly COT download (free, cross-platform). Swapping the price provider does not
   touch it.
4. **The only price-provider volume/OI dependency was an orphaned feature.** The intra-week gap
   estimator (`estimate_current_gap_positions`) read daily `Volume` and provider `Open Interest`
   to nowcast positioning between COT releases. Its output is unreachable from the current UI
   (the dropdown options that surfaced it were removed; `LIVE_PRICE` and `synthetic_daily` are
   written but never read), yet it still executes on an ungated path and is the sole reason the
   provider needs volume at all. It is removed as a prerequisite of this ADR.

With (4) removed, the price-provider contract for the whole toolchain reduces to a single
deliverable: **supply back-adjusted daily OHLC.**

## Decision

**One provider owns each symbol's price series end to end. No provider change ever happens
*within* a single series.**

1. **Per-symbol provider selection.** Which vendor prices a symbol is a *deployment* choice, not
   a fixed identity fact (the same ES is Norgate for local research and databento on the server),
   so it is resolved at runtime from three inputs rather than baked per-symbol in the shared,
   identity-only registry: a **deployment default** (`COTDATA_PRICE_SOURCE`, `norgate` if unset),
   per-symbol **capability** (the `norgate` / `databento` / `yahoo` mappings, `null` where a vendor
   has no series), and an optional per-symbol **override** (`price_source`). The resolver uses the
   override if set, else the deployment default when it can serve the symbol, else falls back to
   yfinance where a `yahoo` ticker exists. Different symbols may resolve to different vendors; no
   symbol is ever a blend.

2. **Databento is a two-stage producer: `ingest` then `build`.** Databento is billed per query,
   so ingestion (the paid, external step) is separated from transformation (free, local,
   re-runnable), mirroring how Norgate's local database is a raw landing zone that the Norgate
   provider only transforms.
   - **`ingest`** fetches raw `.n.0` + `.n.1` `ohlcv-1d` and `statistics` into an immutable,
     append-only **raw store**, recording per-symbol fetched date ranges so a re-run never
     re-pulls a range already held. This is the only step that costs money.
   - **`build`** reads only the raw store, computes `backadj`/`unadj`, and writes the `cotdata`
     store via `store.write_prices(..., source="databento")`. Zero API cost, infinitely
     re-runnable, which is what makes the back-adjustment dev/test cycle free.
   - The raw store is **producer-internal**, not a consumer contract. It lives under its own
     path (`$COTDATA_DATABENTO_RAW`, or a `_raw/databento/` namespace) and is excluded from any
     consumer sync. Consumers keep reading only `prices/`.

3. **Back-adjustment from databento's own continuous series.** Additive (Panama) back-adjustment,
   matching Norgate's method exactly: at each roll, measure the gap on the roll date (the last
   session the expiring contract is front, with the switch assumed at its close) as
   `new_close − old_close`, i.e. `.n.1 − .n.0` on that date, then shift **all** prices up to and
   including the roll date by that gap so the seam closes. (Norgate's worked example: CL rolling
   2019-07-19, old close 55.63, new close 55.76, gap +0.13, so every prior price is adjusted up
   0.13.) Accumulate the gaps back-to-front so the most-recent segment stays at real prices. This
   makes `backadj` mean the same thing regardless of provider; `propadj` is still derived on read
   from `unadj` + `backadj`. `Close` uses databento's settlement statistic (`stat_type 3`) so the
   series is settlement-based, not last-trade. Databento's continuous roll calendar sets the roll
   dates, so exact level parity with Norgate is a validation target (action item 6), not a
   guarantee, the series is internally consistent either way.

4. **Coverage falls back per symbol, still without stitching.** Markets GLBX.MDP3 does not carry
   (ICE softs CC/OJ/SB/KC/CT, lumber) take `price_source: yfinance` and yfinance owns their
   series end to end. Symbols that genuinely need pre-2010 history keep `price_source: norgate`.

The result is symmetric across providers, with one paid boundary each and an unchanged consumer
contract:

```
Norgate:   Norgate local DB   → [norgate build]   → store/prices/*.parquet
Databento: _raw/databento/*   → [databento build] → store/prices/*.parquet
```

Each deployment builds its own store with the provider it can reach: local research → Norgate,
server dash → databento (+ yfinance for the few markets databento lacks). The complete toolchain
runs on either vendor.

## Options Considered

### Option A: single provider per symbol, no cross-vendor stitching (recommended)

Each series is built end to end by one provider. Databento does its own full back-adjustment
from `.n.0`/`.n.1`. Internally consistent by construction, clean provenance, no handoff logic.

### Option B: Norgate-seed + databento-forward (hybrid)

Seed the server store once from Norgate's deep history, then extend it nightly with databento.
Rejected: it puts two vendors inside one series, which forces a handoff offset on every append,
a re-anchoring pass over history on each post-seed roll, and a settlement-basis drift guard. All
that complexity exists only to paper over a within-series provider change, in exchange for
pre-2010 history the public dashboard does not display. The one-time seed also re-introduces a
Norgate touch the server is trying to shed.

### Option C: display-only price overlay (plots use databento, metrics stay Norgate)

Rejected: in `cot-analyzer` the plotted price and the metric-input price are the *same column*
in the same dataframe (`CotIndexer` merges `get_prices(...,'backadj')` into `CLOSING_PRICE`,
then both the metrics and the plot traces read it). A separate display price would visibly
diverge from the metric shading drawn on the same axes, and it would not remove the server's
Norgate dependency (metrics still need the Norgate store).

### Option D: yfinance-only server

Rejected as a general source: no Open Interest, no true futures back-adjustment, research-grade
feed with silent revisions. Fine as the per-symbol fallback for markets databento lacks, not as
the primary provider.

## Trade-off Analysis

| Dimension | Norgate | Databento (Option A) | yfinance |
|---|---|---|---|
| Runs on the Ubuntu server | No (Windows) | Yes | Yes |
| Back-adjusted continuous | Yes (`_CCB`) | Yes (built from `.n.0`/`.n.1`) | Opaque / ETF has no roll |
| Settlement close | Yes | Yes (`stat_type 3`) | No (last trade) |
| History depth | Deep | Floor 2010-06-06 | Varies |
| Symbol coverage | ~47 | CME complex; not ICE softs/lumber | Broad, low fidelity |
| Provider volume/OI needed | No (after the estimator removal) | No | No |
| Cost | Paid (Windows sub) | Paid per query, daily bars are cheap | Free |
| Within-series stitching | None | None | None |

Databento's one gap versus Norgate is history depth. Its cost is bounded: `ohlcv-1d` is the
cheapest schema, the back-adjustment needs only two daily continuous series per symbol fetched
once, and after the raw-store backfill the nightly cost is one bar per symbol plus a roll spread
about once a quarter.

## Consequences

**Positive**

- The server dashboard runs standalone on databento, no Windows/rsync dependency for prices.
- Each price series is internally consistent and single-source, with honest provenance in the
  manifest (`source`).
- The raw store makes the back-adjustment dev/test cycle free and makes Norgate-parity
  validation cheap to re-run offline.
- The toolchain is usable end to end on either vendor, which was the point.

**Negative / limits**

- Databento-sourced price charts start at 2010-06-06. Acceptable for the public COT dashboard
  (lookbacks are in weeks, the visible chart is a few years) but must be confirmed against what
  the charts actually render. Pre-2010 needs keep a symbol on Norgate.
- ICE softs and lumber ride research-grade yfinance on a databento server.
- `unadj`/`propadj` are only maintained where a producer writes them. The dashboard uses only
  `backadj`, so this is fine server-side; flag it if a `propadj` consumer (e.g. DC/Class III
  Milk) ever moves onto the databento store.
- The back-adjustment must be built and validated to match Norgate before the server trusts it.

## Action Items

1. **Remove the orphaned estimator (prerequisite). — DONE (2026-07-23).** Deleted
   `estimate_current_gap_positions` and the `*_NET_EST` / `*_IDX_EST` / `LIVE_PRICE` /
   `synthetic_daily` machinery in `cotmetrics/CotIndexer.py`; removed the "Estimated Indexing" /
   "Net Estimated Positioning" plumbing and `estimate_gap` in
   `cot-analyzer/pages/analytics/positioning.py`; updated `cotmetrics/tests/test_basis.py`.
   cotmetrics 231 / cot-analyzer 57 tests green. This dropped the last provider volume/OI dependency.
2. **Registry provider selection. — DONE (2026-07-23).** Added the `databento` capability mapping
   (default = internal root, `null` for ICE softs / lumber / MSCI intl) and an optional
   `price_source` override to the `Symbol` dataclass and `registry.yaml`, plus `resolve_source()`
   and `default_price_source()` (env `COTDATA_PRICE_SOURCE`). cotdata 94 tests green. Producer
   dispatch in `update.py` lands with the databento producer (items 3–4).
3. **Databento `ingest` stage. — DONE (2026-07-23).** `providers/databento.py` gained a Stage-1
   `ingest()` that pulls `.n.0` / `.n.1` `ohlcv-1d` + `statistics` into an append-only raw store
   (`$COTDATA_DATABENTO_RAW`, else `_raw/databento` under the store) with a fetched-range manifest
   that resumes each feed from `last_date + 1`. The client is injectable, so it is fully tested
   without an API key or network (4 tests). cotdata 98 tests green. Producer-internal — exclude
   the raw store from consumer sync.
4. **Databento `build` stage. — DONE (2026-07-23).** `build()` reads the raw store and writes
   `unadj` (raw front continuous, settlement close) + `backadj` (additive back-adjustment: gap =
   `n1_settle − n0_settle` on each roll date, every price up to and including it shifted, gaps
   accumulated back-to-front) via `store.write_prices(..., source="databento")`. Settlement `Close`
   from `stat_type 3` (dated by ts_ref), OI from `stat_type 9`; roll detection keys on the front
   contract **`instrument_id`** change (databento's `symbol` column is a constant continuous alias,
   so it cannot be used — found on the first live run), with a loud warning if none are found.
   Verified end-to-end through `get_prices` (5 tests) and on a live ES/CL/GC ingest+build (65/80/235
   rolls; offset piecewise-constant stepping exactly on roll days; daily changes preserved off-seam;
   newest segment anchored to real prices; no non-positive closes). cotdata 110 tests green.
5. **yfinance coverage. — DONE (2026-07-23; lumber ticker corrected 2026-07-27).** Added Yahoo
   continuous tickers for the ICE softs (SB/CT/CC/KC/OJ) and lumber in `registry.yaml`. Lumber is
   `LBR=F` (the current CME physical contract, verified live); the originally-recorded `LBS=F` was
   the delisted random-length contract, dark since 2023-05. DX (ICE US Dollar Index, not on
   GLBX.MDP3) was also set `databento: null` with `yahoo: DX-Y.NYB`. No `price_source`
   override is used: they keep `databento: null`, so capability alone resolves them to yfinance on
   a databento deployment while they stay on Norgate locally. The yfinance producer now filters its
   targets by `resolve_source(sym, default_price_source())`, so it never overwrites Norgate's softs
   on a local box.
6. **Validation harness. — DONE (2026-07-27).** Run against a full databento-built store versus
   the Norgate store. Result in `cotdata/docs/databento_norgate_parity.md`: of 40 databento
   symbols, 14 match Norgate cleanly, SI/HG/6J were a pure unit convention reconciled with a
   build-stage scale, and the monthly-roll commodities and livestock differ because the two
   providers roll their continuous series on different calendars. A follow-up roll-rule
   investigation (`scripts/investigate_databento_roll_rule.py`) found the roll rule is per-symbol,
   not global (energy `.c`, grains `.v`, quarterly `.n`). See the Outcome section for the
   disposition.
7. **CLI + dispatch + docs. — DONE (2026-07-23).** Added `cotdata-update --ingest-databento`
   and `--build-databento` to `update.py`. Producer dispatch is by `resolve_source` /
   `default_price_source`: the yfinance and databento producers write only the symbols that
   resolve to them on the deployment and honor the `price_source` override. Documented the
   two-stage model, the raw store, and `COTDATA_PRICE_SOURCE` in the README (Providers &
   authentication) and the sources table. cotdata 103 tests green.

## Outcome (2026-07-27)

The architecture above (one provider per symbol, the two-stage `ingest` then `build` producer,
additive back-adjustment from databento's own continuous series) was implemented in full and
validated against Norgate. It works. databento produces internally-consistent back-adjusted
series through the unchanged consumer contract, and the whole toolchain runs on it end to end.

**Validation result** (see `cotdata/docs/databento_norgate_parity.md`). Of 40 databento-built
symbols, 14 match Norgate cleanly. SI, HG, and 6J were a pure unit convention (Norgate quotes
silver and copper in cents and JPY in the IMM x100 form), reconciled with a build-stage price
scale. The monthly-roll commodities and livestock differ because databento and Norgate roll
their continuous series on different calendars. A follow-up investigation
(`scripts/investigate_databento_roll_rule.py`) showed the right roll rule is per-symbol, not
global. Energy (CL, NG) tracks Norgate on databento's calendar roll `.c` (near-perfect). Grains
(ZS, ZC) track best on the volume roll `.v` (better than the default `.n`, but loose, a few days
off each roll, so about 0.95 not 0.999). The quarterly financials and metals already match on
the open-interest roll `.n`.

**Deployment decision.** The server will NOT source databento. It keeps sourcing Norgate via a
Windows-to-Linux store sync, the rsync link this ADR originally hoped to drop. Rationale: the
sync is risk-free (the dashboard then shows exactly what local research shows, the same Norgate
data) and lower maintenance than carrying a per-symbol roll-rule table plus a grain series that
never quite matches. The per-symbol roll rule is documented but intentionally not built.

**Disposition.** The one-provider-per-symbol architecture and the databento producer are Accepted
and merged. databento stands as an available, validated ALTERNATIVE provider that produces
provider-different (not Norgate-identical) series, the expected behavior when changing data
vendors. It is not positioned as a Norgate drop-in for the server. The raw databento bronze store
is kept so the capability can be picked up again later without re-paying the backfill.
