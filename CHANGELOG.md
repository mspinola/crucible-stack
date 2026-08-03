# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning and what counts as a
breaking change are governed by [docs/api-stability.md](docs/api-stability.md).

## [Unreleased]

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

### Added
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
- **The `crucible` floor is raised to `>=0.5.0`** for the monitor API above. Unlike the
  0.3.0 constraint, this one is **not yet true**: the monitor is in crucible's
  `[Unreleased]` and the newest published crucible is 0.4.0. This must not be released
  before crucible 0.5.0 is on PyPI, or `import crucible_stack.orchestrate` raises for
  anyone installing from PyPI. `tests/test_crucible_compat.py` gains two checks that name
  the cause, including one asserting `edge_monitor` still has no parameter from which a
  baseline could be rebuilt, since a future crucible relaxing that would silently make
  `EdgeDecayTrigger` incapable of firing while every other test kept passing.
- `crucible>=0.3.0` (was `>=0.2.0`). The honest-N API this package depends on landed after
  crucible's v0.2.0 tag, so the old constraint was satisfiable by a version that could not
  actually satisfy it. The CI workaround that installed crucible from git is gone.
