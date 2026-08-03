# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning and what counts as a
breaking change are governed by [docs/api-stability.md](docs/api-stability.md).

## [Unreleased]

## [0.2.0] - 2026-08-02

The deployment-monitoring release. 0.1.0 shipped the define / search / size / deploy
framework; this adds the half that runs *after* a promotion, and the API-stability
policy that says what any of it promises.

`drift` already watched the equity **path**. This adds the per-trade **parameter**:
`orchestrate.decay` over crucible's edge monitor, `EdgeDecayTrigger`, an `EdgeBaseline`
frozen into the `DeploymentLedger` beside the `DriftEnvelope`, and the `--edge-decay` /
`--arl0-years` flags that drive them. The two monitors come apart in both directions, so
running only one leaves a real failure invisible.

**`--edge-decay` is off by default, and the order matters.** `EdgeDecayTrigger` fails
open, so enabling it before a promotion has written a baseline makes every cycle fire and
re-optimize, and every promotion freezes a *new* envelope. The drift envelope would then
be discarded on every tick and never allowed to mean anything, which is the re-baselining
trap both monitors are built to refuse, arriving through the back door. Let one promotion
write a baseline first.

### Added
- **`--arl0-years`**, exposing the edge-decay CUSUM's false-alarm budget on the CLI.
  `EdgeDecayTrigger` has always taken a `Thresholds`, but `__main__` built it with none,
  so the budget was unreachable from the only place the monitor actually runs. It is the
  one knob worth reaching for, because it buys detection latency roughly one for one:
  measured on a real 47-market trend book (23.3 trades/yr), 25 years gives a 9.4-year
  wait to call a halved edge, 10 years gives 5.1, and 6 gives 3.5. On a low-frequency
  book the default can be slow enough to be decorative, so the number deserves choosing
  rather than inheriting.

  Omitting it passes `None` rather than a materialized `Thresholds()`, deliberately: the
  default then comes from whichever crucible is installed, so a retune there reaches this
  CLI without a matching change here.

  Two silent-no-op paths are reported rather than shrugged at, since a tuning flag that
  tunes nothing reads as applied: passing it without `--edge-decay`, and passing it when
  the frozen baseline carries no firing rate (years are converted using that rate, so
  crucible falls back to `monitor_arl0_trades` and the flag does nothing). A non-positive
  budget raises, since it is a span of calendar time.

- **`crucible_stack.orchestrate.decay` and `EdgeDecayTrigger`**: the parameter-space
  counterpart to `drift`. `drift` watches the equity **path** (cumulative R and drawdown
  against the frozen envelope); this watches the per-trade **parameter** (expectancy, and
  how often the signal fires). They are complements and come apart in both directions: a
  halved expectancy leaves the path flat and usually inside a p5 band provisioned over a
  multi-year horizon, and a run of correlated losers breaches the drawdown floor with
  every per-trade statistic intact. Running only one leaves a real failure invisible.

  The judging is crucible's (`crucible.validation.monitor`). What this contributes is the
  three things crucible refuses to own: freezing an `EdgeBaseline` at the moment of
  promotion, persisting it in the `DeploymentLedger` beside the `DriftEnvelope`, and
  turning a verdict into a re-optimization trigger. `DeploymentEntry` gains a `baseline`
  field that round-trips through JSON, and a refusal carries none, matching the envelope
  rule so there is never a reference a later cycle could re-baseline onto.

  **Only DEGRADED fires.** crucible reserves that label for the CUSUM, the one channel
  with a stated false-alarm rate. SLIPPING is reported in `reasons` and does not trigger.
  Promoting an uncalibrated tripwire to a re-optimization trigger would undo the reason
  crucible separates them, and would tax the honest N besides: every re-optimization is
  variants added to the `SearchSpaceLog`.

  `TriggerContext` gains `trade_r` (per-TRADE R since promotion) as a field separate from
  `realized_r` (PERIODIC R, the grid the envelope was built on). Two different series with
  two different lengths and two different clocks (`trades_live` vs `elapsed`); conflating
  them is the units bug crucible fixed in v0.4.0. `run_cycle` gains an optional `trade_r`
  argument, defaulting to empty, so existing callers are unaffected.

  `trigger` depends on `decay.check_decay` rather than on crucible directly, exactly as it
  depends on `drift.check_drift`, which keeps the trigger seam's import surface to
  crucible_stack + numpy as `test_triggers_know_nothing_about_the_substrate` pins.

- **`tests/test_no_findings_in_prose.py`**, a guard on the writing rather than the code.
  The boundary guard reads the syntax tree, so it cannot see a docstring or a markdown
  file, and every leak found while extracting this framework came through prose. This
  scans for the *shape of a result* (money, measured edges, private strategy identifiers,
  verdicts attached to a named book) rather than for strategy vocabulary, which would fire
  on honest provenance notes and get itself deleted. Regression-tested against the real
  leaks that occurred, and against ordinary framework prose that must not fire.
- **[docs/api-stability.md](docs/api-stability.md)**, the API stability policy, pinned by
  `tests/test_public_api.py`. What is public is exactly what a listed module names in
  `__all__`; registry *contents*, numerical output and exception messages are explicitly
  outside the promise.
- `crucible_stack.orchestrate` now exports `run_cycle`, `CycleResult`, `Reoptimization` and
  `missed_windows`. `Reoptimization` is the return type a book adapter must construct, so
  its absence made the orchestrator's protocol unimplementable without reaching into a
  submodule.
- `__all__` on `framework.config`, `framework.montecarlo`, `framework.strategy`,
  `optimize.select` and `engine.simulator`, which previously had no declared surface.
- `framework.montecarlo.max_drawdown`, public. It was `_max_drawdown` and already had a
  downstream consumer, so it was public in fact and private only in name.
  `_max_drawdown` remains as a deprecated alias.

### Changed
- **The `crucible` floor is `>=0.6.0`** (was `>=0.2.0` at 0.1.0). It moved three times
  across this release and the intermediate notes are collapsed here rather than left
  looking live: `>=0.3.0` for the honest-N API, `>=0.5.0` for `validation.monitor`
  (`EdgeBaseline` / `edge_monitor`), and `>=0.6.0` for
  `Thresholds.monitor_arl0_years`, which `--arl0-years` constructs. All three are true
  as of this release; crucible 0.6.0 published 2026-08-02.

  The `>=0.5.0` step carried a warning that it was not yet satisfiable, which is why
  the CI workaround that installed crucible from git is gone. The `>=0.6.0` step was
  briefly *wrong* rather than aspirational, and that is the more useful lesson: the
  flag merged while the pin still said `0.5.0`, whose `Thresholds` has no such field.
  A floor is only ever exercised by the version you do NOT have, so with every local
  checkout on 0.6.0 nothing failed. `tests/test_crucible_compat.py` now reads the
  declared floor out of `pyproject.toml` and fails when it falls behind the APIs the
  package uses, alongside its existing checks that the monitor surface is importable
  and that `edge_monitor` still has no parameter from which a baseline could be
  rebuilt (a future crucible relaxing that would silently make `EdgeDecayTrigger`
  incapable of firing while every other test kept passing).

### Fixed
- **The edge monitor's firing-rate channel was dead through the orchestrator.**
  `check_decay` built its `TradeLog` from bare floats (`TradeLog.from_arrays(trade_r)`),
  while crucible derives the live firing rate from the log's `entry_date`. So
  `live_trades_per_year` and `frequency_ratio` came back `None` on every cycle, for every
  book, however carefully the baseline's own rate had been frozen at promotion. Two of the
  monitor's three channels ran; the third reported itself off and nothing said why.

  It is the channel that matters most on its own, because neither of the others can see
  the failure it covers: a signal that stops firing while the trades it still takes keep
  their per-trade edge. Expectancy is unchanged, so the CUSUM stays quiet and the rolling
  window reads full size, and annual R falls anyway because the opportunity set shrank.

  `check_decay` now takes `trade_dates`, `TriggerContext` carries them (refusing a length
  mismatch against `trade_r`, which would mis-date the rate rather than fail), `run_cycle`
  forwards them, and `EdgeDecayTrigger` passes them through. The CLI sources them from an
  optional `trade_dates_since(since, params)` on the book. Absence stays legal and is now
  *reported* rather than silent, since a book that cannot date its trades is still worth
  monitoring on expectancy. No crucible change was needed: `TradeLog.from_arrays` has
  accepted `entry_date` all along.

## [0.1.0] - 2026-07-23

First release. The define / search / size / deploy framework, strategies not
included. No changelog section was written at the time; this heading exists so the
published version is on the record.
