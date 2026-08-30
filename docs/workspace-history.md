# Workspace history: the archaeology behind the CLAUDE.md rules

This file holds the narrative that used to live inside the workspace `CLAUDE.md`: the record of
what that file previously claimed, what each wrong claim cost, and how each correction was
established. It was extracted on 2026-08-29 when the file was trimmed to rules only (work order:
npf `docs/handoffs/2026-08-29-claude-md-trim-v2.md`; the full pre-trim text is preserved at the
workspace root as `CLAUDE.md.bak-2026-08-29` and the accretion existed because the file was
untracked, so corrections had nowhere else to live).

**The rule going forward:** when a claim in `workspace-CLAUDE.md` turns out wrong, fix it there
in place (the git history now records what it used to say) and, where the story of the mistake
is itself instructive, add a dated entry here. Do not narrate corrections inside the rules file
again.

Entries are grouped to mirror the rules file's sections. Text in quotes is carried from the
pre-trim file substantially verbatim.

## The month-end pin and the equities half

**2026-08-20: the live equities half stopped being frozen, and the pin moved.** For weeks the
file said the six pinned symbols must never be refreshed and that `marketdata-update
--verify-pin` against `~/code/marketdata_store` was the check to re-run. Both halves of that
went wrong at once: a NEW scheduled equities task on the producer ran an unscoped
`marketdata-update --bars --domain equities` and mirrored the result to every replica, moving
the live equities half from 2026-07-24 to 2026-08-20. "The study did not fail, which is the part
worth remembering. It returned a quietly different number, a pre-registered sample size of 288
against 287, because one more month-end had entered the window." The data could not be re-fetched
back: Yahoo dropped four bars from its own history in the interim (AGG 2026-07-21 and
2026-07-22, IEF 2026-07-22, SHY 2026-07-21), so only a frozen copy reproduces the published
figures. The response was to move the study off the live store rather than keep re-freezing it:
the frozen copy was made 2026-08-20 23:17, npf #208 (`4decf21`) made `load_pair` verify and
raise, and npf #209 repointed the evaluator's header. Verified 2026-08-22: live store drifted on
all six symbols (`last_date` 2026-07-24 -> 2026-08-21), frozen copy `pin OK`. From that date
onward, drift on the live store is the expected answer and not a fault.

**2026-08-09: the `/MIR` deletion that created the mirror rules.** Until then the Windows store
held only futures while the Mac also carried the frozen equities half, and a whole-store
`robocopy /MIR` from Windows **deleted the Mac's equities half, pin included**, because `/MIR`
purges whatever the source lacks. Recovered in full from `~/code/cot-backup`, which had every
pinned symbol with `updated_at` matching exactly. The four CBOE implied-vol indices were not in
that backup and were let go at that moment, "which is the whole of the evidence this file later
inflated into 'unreferenced'". The fix was to seed the frozen equities half onto Windows so a
whole-store mirror is meaningful; the two standing mirror rules date from here.

**2026-08-20/21: the first mirror rule fired.** `EEM` and `EFA` were seeded on the Mac on
2026-08-20 and not on Windows, and a mirror removed them (2026-08-21: 13 equities, manifest
listing neither, MFS and MME back to no prices). They were then seeded on WINDOWS and the 17:30
run delivered them; verified 2026-08-22, 15 equities, proxies resolving to real bars. "The
lesson is the ordering, not the symbols: waiting for the producer worked, and hand-restoring on
the Mac is what created the one-sided state in the first place."

**The expired half of the `--domain` rule.** The original reason for scoping was that an
unscoped run would refresh the six pinned symbols and break the snapshot at the source. Since
2026-08-20 the snapshot's authority is the frozen copy, so that harm is gone and the nightly
equities task refreshes the six by design; what survives is the scheduling reason now stated in
the rules file. An earlier version of the file also claimed `run-prices.cmd` carried a stale pin
comment and sent a reader to the box to fix it; **there has never been one**. That wrapper's
header explains `--domain futures` in terms of ADR-0007, which is correct and needs no change.

## The CBOE implied-vol indices

Deleted twice on the same false signal and restored twice, the second time on 2026-08-21
(marketdata #19 dropped them, #21 reverted hours later). "Both removals rested on a clean grep
of `marketdata` for a consumer that lives in `npf`. `marketdata` is public, `npf` is private,
and `trading_riot` is tracked inside it, so no search of the producer can ever see the
dependency." What made the trap efficient was CLAUDE.md itself: the file called the four
"unreferenced" (an inflation of the 2026-08-09 backup gap above), "so each session found written
confirmation of the thing it was about to get wrong. That trap caught two separate sessions
inside twelve hours." The resolution moved the authority into `registry.yaml` inline beside the
symbols, with the exit condition stated there, which is the pattern the trim generalised: facts
about code belong next to the code.

## ADR-0007 and the price-code moves

The file spent two weeks saying ADR-0007 was "accepted as a direction, only step 1 has shipped,
no code has moved between packages"; all three clauses had gone false by 2026-08-09. The
sequence: cotdata #105 deleted `providers/norgate.py`, `prices.py`, `providers/yfinance.py` and
their tests (~2,400 lines); #108 removed the `cotdata-prices` entry point and, hours after #105,
removed databento outright; marketdata #15 ported databento as "step 2, the last part".
`marketdata/scripts/import_from_cotdata.py` was the bridge for exactly the window when the read
had moved and the write had not, and is spent.

The per-expiry paragraph is the same story in miniature: the file said per-expiry OHLC "is not
fetched let alone stored", and the first half is false. It IS fetched on every futures producer
run and discarded down to Date/Volume/Symbol. "The old wording sent a reader looking for a data
source that is already paid for and already arriving"; the blocker is a column selection, a
schema decision not yet made, recorded in `marketdata/docs/design.md` under Known holes.

## The edge monitor

The file said `npf/scripts/orchestrate_trend.sh` did not pass `--edge-decay`; it has passed it
since 2026-08-02, turned on only after a promotion had written a baseline to the ledger, which
is the order the trap is about. Verified 2026-08-20: the ledger's last entry (2026-08-02,
`promote`, verdict TRUSTWORTHY) carries both a `baseline` and an `envelope`. The rule survives
in the rules file because it is still the rule for the next book.

The two field findings (calendar-time ARL0 denomination; skew inflating ARL0 ~1.1x at +3, ~2.0x
at +5 on the real pooled book, ~4x at +11, while ARL1 tracks nominal because in-control alarms
arrive via rare excursions and under-shift alarms arrive by drift) were both missed by a fully
green test suite and settled by measurement against the real 47-market universe. "Reasoning by
analogy got this backwards twice before measurement settled it." Detail:
`crucible/docs/edge_monitor.md`.

## Environments and publishing

**The phantom-package note ate its own example.** It used to cite `marketdata` as the phantom
and quietly stopped being true when marketdata was installed, "which is the failure mode the
note is about". The example was switched to `livebook`.

**Tagged is not published, both directions, both in one week (2026-08-02).** The file's worked
example said `v0.2.0` was tagged while PyPI carried 0.1.0, and concluded a symbol added since
0.1.0 had no external consumers by construction. That inverted when cotdata 0.3.0 shipped
through the new tag-triggered Trusted Publishing workflow (cotdata #94; #95 rewrote
`WINDOWS_SETUP.md` because its install-from-a-clone advice had lost its reason), so the lesson
survived while its example flipped from "deletion is free" to "deletion is breaking". The same
day, `crucible` proved the converse: `pyproject.toml` said 0.4.0 and `CHANGELOG.md` carried a
dated `## [0.4.0] - 2026-07-28` section formatted like every shipped release, but no `v0.4.0`
tag was ever pushed, so PyPI went 0.3.1 -> 0.5.0 -> 0.6.0 and a units fix to the detrended null
sat unpublished for a week while the changelog implied it was available. Nothing surfaced it
until a crucible-stack floor of `crucible>=0.5.0` failed to resolve.

**2026-08-16: the ruff pin drift.** npf's venv was found carrying `0.16.0` against its own
`0.15.22` pin, so a local npf lint was quietly not the check CI runs. Fixed the same day; the
"verify with `python -m ruff --version`, never the pin" rule dates from this. Same class as the
`pip list` hazard.

## Launchers and test guards (the ADR-0007 aftershocks)

**2026-08-14: the week of silent failures.** The file used to note, parenthetically, that a
launchd job would not see `MARKETDATA_STORE` because nothing marketdata-backed was scheduled on
the Mac. That stopped being true when ADR-0007 moved the price read, "and it cost a week of
output before anyone looked": `npf-weekly-setups` wrote no setup lists for the 2026-08-11 week
and `npf-orchestrate` logged `cycle FAILED` with no drift check run, the first Friday after the
read path moved. Neither launcher defaulted the variable, launchd reads no shell profile, and
the store raises rather than defaulting, "so the failure was loud in a log nobody was reading
and invisible everywhere else". Both launchers now default it (npf #205). `COTMETRICS_PARAMS`
matters in the same list because a launchd job that fell back to the sample universe would
re-optimize and record a verdict on a handful of markets.

**2026-08-20: the test guards, and the innocent file this file blamed.** The file said
`test_books_month_end.py` read `MARKETDATA_STORE` at import time. That was never the mechanism:
that file and `test_books_treasury_seasonal.py` skip cleanly. The 20 failures came from
`test_books_cmr.py` and `test_books_trend.py`, whose `needs_data` guard keyed on
`COTDATA_STORE` alone, so with the price store unset the tests ran into a `RuntimeError` deep in
`marketdata.config`. Both guards now name both stores (npf #207). "That was the THIRD failure in
one day from the same cause": ADR-0007 moved the read, and two launchd launchers (npf #205) plus
these two guards each had to be chased separately, none failing at the seam. The grep-the-old-
store rule in the rules file is the distillation.

## The Windows producer

**"Restart on failure" was wrong for weeks, and was measured wrong on the box.** Task
Scheduler's restart-on-failure does not fire on a non-zero exit from the action; it covers the
engine failing to LAUNCH it. A run whose action returns 1 is logged as event 102, "Task
Scheduler successfully finished", and nothing is rescheduled. "Confirmed against 9 days of
history for `cotdata prices`, which deferred on four consecutive nights (2026-08-12 to 15) and
was launched exactly once on each of them. So a whole category of 'the gate will retry'
reasoning in this file was describing a retry that did not exist." The repeating trigger is the
real mechanism, and `verify-scheduling.ps1` now enforces its presence and warns on any
`RestartCount > 0`.

**The two-script delivery split that never existed.** The file described `sync-store.cmd` for
COT and a `sync-marketdata.cmd` for bars; there is no `sync-marketdata.cmd`, and the coupling it
implied was designed out. The real design (each of `sync-store.cmd` and `push-to-server.cmd`
mirrors BOTH stores, three wrappers call the pair) is in the rules file. The Friday poller's
guard keys on `status.json`'s `newest_data` for measured reasons (2026-08-21): `manifests/
cot.json`'s MD5 changes on a no-op repeat because every entry is rewritten with a current
`updated_at`, and a `%~tF` timestamp has one-minute resolution and would compare equal to a
capture landing in the same minute. Before the guard, 23 unguarded Friday repeats scanned the
~1 GB/year vintage tree over SMB and again over SSH for nothing.

**`--final-cutoff`**: the wall-clock version it configured deferred every attempt on 2026-07-27
when Norgate published at 8:49pm against a 20:55 threshold; the data-asking `--require-final`
replaced it and the flag is accepted and ignored.

**Observed healthy-defer example (2026-08-22)**: `cotdata prices` last ran 01:55 and returned 1,
that being the final repeat of the 20:55 window finding the store already current; the capture
had happened hours earlier and the store carried Friday's bars. Full box verification the same
day: 30 pass, 0 warn, 0 fail; all five tasks enabled with expected arguments, both polling tasks
carrying their repetition, no `RestartCount` set anywhere, replica parity exact (123 mirrored
parquet in `cotdata_store`, 114 in `marketdata_store`, both sides).

**The ERRORLEVEL trap's discovery**: without per-step capture, a permanently failing sync reads
as success every night, "which is how the `/MIR` deletion above went unnoticed until it was
looked for".

## The held-out set

The file said seven of the 47 universe markets were `heldout` for weeks; parsing
`params.yaml` gives five (EMD, NKD, MME, MFS, KE), of which three are usable. "The wrong count
had already cost one spec a design assumption" (npf
`docs/handoffs/2026-08-25-short-side-asymmetry.md` F14). Verified 2026-08-25 by parsing
`params.yaml` and `registry.yaml`.

## crowdmon

**The "is anything still running" question was answered wrong twice.** The section first said
two launchd jobs and the `/damage` page were live and the question undecided; it was then edited
on 2026-08-09 to say the jobs were still running, "written without checking the machine, which
is the failure this file's own 'measure, do not assume' rule exists to prevent". Checked
2026-08-08: `launchctl list` carries no crowdmon label and no plist exists;
`com.mspinola.crowdmon-publish` and `com.mspinola.crowdmon-live-tests` were unloaded and deleted
that day (`live-tests` had been failing every morning on the drifting live pins);
`cot-analyzer`'s `/damage` page went in that repo's #22 and #25. `~/code/crowdmon_store` is
written by nothing and read by nothing, left in place because deleting it is not reversible.
`crowdmon/DEPRECATED.md` §3 was correct the whole time.

**The drifting live pins**: 6 of 654 tests fail, all live pins comparing a recomputation against
counts frozen when the store held one fewer week; it worsens every week the store advances.
`DEPRECATED.md` §2.1 says the fix is to neutralise the pins rather than chase them.

**The one open handoff went moot**: `2026-08-06-trigger-contradicted-copy.md` was a copy fix to
the `/damage` page, deleted 2026-08-07. Nothing left to fix; the register is closed.

## The trim itself

2026-08-29: the file measured 911 lines, 9,728 words, with at least six paragraphs of the form
"this paragraph used to say X and was wrong". Root cause: the workspace root is not a git repo
and CLAUDE.md was untracked, so every correction had to preserve the superseded claim in prose.
Resolution: canonical file tracked at `crucible-stack/docs/workspace-CLAUDE.md`, workspace-root
path becomes a symlink to it, archaeology moved here, facts about code pushed next to the code
where already true (registry.yaml, wrapper headers, `costs.py`). Work order and pre-edit triage:
npf `docs/handoffs/2026-08-29-claude-md-trim-v2.md`.
