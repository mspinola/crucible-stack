# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning and what counts as a
breaking change are governed by [docs/api-stability.md](docs/api-stability.md).

## [Unreleased]

### Added
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
- `crucible>=0.3.0` (was `>=0.2.0`). The honest-N API this package depends on landed after
  crucible's v0.2.0 tag, so the old constraint was satisfiable by a version that could not
  actually satisfy it. The CI workaround that installed crucible from git is gone.
