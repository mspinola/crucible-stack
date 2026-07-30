# ADR-0008: COT vintage provenance stays in cotdata, persisted in Parquet

> **Scope.** Like ADR-0006 and ADR-0007, this is a data-layer architecture decision recorded in
> the toolchain ADR log because it interprets a boundary the whole stack reads through.
> Specifically it answers a question ADR-0007 leaves open: now that `cotdata` is being narrowed
> to COT only, is *as-published provenance* for that COT data inside the narrowed boundary or
> outside it. Code changes land entirely in `cotdata`; no consumer read contract changes.

**Status:** Accepted (2026-07-30) as to the decision, with the implementation complete but
**unmerged**: it lives on `cotdata` branch `claude/cot-revision-snapshots-9b196f`
([PR #78](https://github.com/mspinola/cotdata/pull/78)) pending review. This is the ADR-0007
distinction again, in the opposite direction: 0007 was accepted as a direction with most of the
work outstanding, whereas here the work is done and only the merge is outstanding. Acceptance
records the boundary interpretation, which is what future readers of ADR-0007 need, rather than
authorising work still to come.
**Date:** 2026-07-30
**Deciders:** Matt (sole maintainer)

## Context

The CFTC revises COT data after publication. Four mechanisms exist (error corrections, late or
amended filings, contract-universe additions, and trader reclassification) but only the last one
matters analytically: reclassification moves positions *between categories* retroactively. Because
downstream signals are rolling z-scores and percentiles computed against years of history, a
restatement silently rewrites the baseline every historical reading was measured against. There is
precedent for this at scale: in July 2008 the Commission revised reports back to 2007-07-03.

The CFTC serves **current state only**. There is no vintage archive and no as-published endpoint,
and git-history archaeology on the `cotdata` repo recovered nothing, so vintage data can only be
accumulated going forward. Every uncaptured week is a permanent blind spot in precisely the part of
the series most likely to have been revised, because revisions cluster near-term.

Two questions sat on ADR-0007's boundary, and answering them was a precondition for building
anything:

1. **Is vintage provenance inside the narrowed `cotdata`?** ADR-0007 shrinks `cotdata` to CFTC
   positioning only and moves all bar data to `marketdata`. A new subsystem arriving in `cotdata`
   during that narrowing needs justifying rather than assuming.
2. **What is the storage format?** The original external specification for this work called for
   DuckDB or SQLite. `cotdata` is deliberately database-free: one Parquet file per symbol plus a
   JSON manifest, and that layout *is* the producer/consumer contract.

## Decision

**1. Vintage provenance is in scope for the narrowed `cotdata`.**

It is CFTC-positioning provenance: the "when did we know this value, and what did it previously
say" for exactly the data ADR-0007 keeps. The narrowing in ADR-0007 is along the axis of
*instrument domain* (COT versus bars), not along the axis of *derived versus raw*. Vintage capture
does not cross the domain axis at all, so it does not touch the seam ADR-0007 draws.

It also has to live beside the fetch. Capture is a producer action, since it records what the
source served at a moment in time. Splitting it from the producer that performs the download would
put the capture layer where it cannot observe the thing it exists to record.

**2. It persists in Parquet plus the existing manifest, with no database.**

Nothing in the bitemporal design requires SQL. Change-only writes over roughly 50k rows per year
keep the entire vintage history in single-digit megabytes indefinitely, because storage grows with
actual revisions rather than with elapsed time. DuckDB can still query these Parquet files ad hoc
whenever SQL is convenient, without adopting a datastore as the *storage format* and without
contradicting the repo's contract.

### Store layout

The vintage subtree is disjoint from the current-state tables, which are untouched:

```
$COTDATA_STORE/vintage/
  raw/{source_kind}/{year}/{retrieved_at}_{sha8}.{ext}   immutable, never rewritten
  observations/report_year=YYYY/*.parquet                change-only bitemporal rows
  revisions/detected_year=YYYY/*.parquet                 append-only, field-level
  release_schedule.parquet
  announcements.parquet
  snapshots.json                                         provenance index
```

Two naming details are load-bearing rather than cosmetic. The provenance index is
`snapshots.json`, **not** `manifest.json`, because the deployment's sync scripts exclude that
filename unanchored (`robocopy /XF` and `rsync --exclude` both match by name at any depth) and
would have stripped it in transit, delivering raw archives to a replica with no index. And
provenance lives in its own file rather than as a block inside `manifests/cot.json`, because
`store.reconcile_manifest` prunes any manifest entry lacking a matching `{name}.parquet`, and raw
snapshot ids are not parquet files.

## Consequences

- **No new runtime dependency.** pandas and `pyarrow>=10` are already present and implement
  change-only inserts and point-in-time reads. The external spec's polars reference was dropped
  for the same reason the database was.
- **Current-state output is byte-identical**, enforced by a golden-file test generated from the
  pre-change tree. Existing consumers see no change whether or not vintage capture is enabled.
- **The subsystem is opt-in**: separate entry points (`cotdata-vintage`, `cotdata-schedule`) and a
  separate scheduled task. A store with capture never run is indistinguishable from before.
- **When ADR-0007 step 2 moves code between packages, the vintage subtree moves with the COT half
  by construction** — it is already on that side of the seam, which is the practical payoff of
  deciding question 1 explicitly rather than by default.
- **Trade-off accepted:** hand-rolled bitemporal logic in pandas instead of SQL. Justified by the
  data volume and by not contradicting the no-database contract. The cost is real but bounded:
  change-only insert, a point-in-time read, and a field-level diff.
- **A deployment constraint is created.** The vintage tree must not be written on a machine whose
  store is mirrored from a producer, because `robocopy /MIR` and `rsync --delete` remove
  destination-only files and this data cannot be re-fetched. Capture runs on the producer, or
  `COTDATA_VINTAGE_ROOT` relocates the tree outside the mirrored store.

## Alternatives considered

- **DuckDB or SQLite, as originally specified.** Rejected: it introduces a datastore the repo
  deliberately avoids, cutting against the Parquet-plus-manifest contract, for no benefit at this
  data scale. Parquet remains directly queryable from DuckDB regardless, so the convenience the
  database was wanted for is not actually lost.
- **Keeping vintage outside `cotdata`** (in `npf`, or a new sibling). Rejected: it is raw-data
  provenance rather than strategy logic, and separating it from the producer that fetches the data
  would place the capture layer where it cannot see the fetch. A new sibling would also duplicate
  the registry and store plumbing for one subsystem.
- **Storing full snapshots per week instead of change-only rows.** Rejected: storage would grow
  with elapsed time rather than with revisions, and the revision set — the actual object of
  interest — would then have to be derived on every read instead of being recorded when detected.

## Open questions

- **Futures-and-options-combined is not captured.** `cotdata` has only ever fetched futures-only,
  and this work deliberately carried that scope forward rather than widening it. `combined` is
  therefore constant-`False` in the natural key today, and half the reportable universe is absent.
  Adding it later is a fetch-list change with no schema migration, because the key dimension is
  already present.
- **Only the Legacy report is wired end to end** for CLI ingest. Disaggregated and TFF have their
  controlled vocabularies declared but no canonicaliser yet. The change-only and revision
  machinery is report-type agnostic once one exists.
- **Whether a revision, once detected, should invalidate downstream caches automatically.** Today
  detection is recorded but nothing consumes it. `cotmetrics` keys its cache on
  `schema_version`, which a vintage revision does not bump.
- **Retention.** Current policy is to keep every raw file forever (roughly 1 GB/year, dominated by
  current-year churn), on the grounds that the weekly copies *are* the vintage series and the
  storage cost is immaterial against irreplaceability. Worth revisiting only if that ratio changes.
