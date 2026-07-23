# AGENTS.md

Guidance for AI coding agents working on crucible-stack. Written by a human, kept short on
purpose. If you are an agent, treat this as constraints, not suggestions, and propose edits to a
maintainer rather than expanding it yourself.

---

## What this package is, and the line it holds

crucible-stack is Pardo's operating system with a significance gate at every step: an optimizer,
crucible, a capital sim, and an orchestrator, each answering one question and refusing to answer
the next (see `docs/toolchain.md`). It is a framework for defining strategies that deliberately
contains none.

Two invariants make that real, and both are enforced by tests. Do not defeat either.

- **Zero strategy-repo imports.** The framework must not depend on any strategy repo. One
  convenient import of a strategy helper turns a public framework into one with a private
  dependency that nobody notices until an install downstream fails. `tests/test_boundaries.py`
  fails loudly on any such import. If you need something from a strategy repo, the dependency
  points the other way.

- **The seam contracts are the invariant, engines are not.** The four data contracts
  (`TrialMatrix`, `TradeLog`, `EquityResult`, `DeploymentLedger`, see `docs/design/seam-contracts.md`)
  are what the layers agree on. Concrete engines live *behind* the seams and stay swappable. Do
  not leak an engine's types across a seam, and do not change a contract's shape as a shortcut,
  the contract is the thing every other layer trusts.

## Respect the layer refusals

The value of the design is what each layer refuses to do. Do not collapse them.

- **crucible stays capital-free.** It is a dependency here, reasoning over a `TradeLog`, never an
  equity curve. Do not feed account-level concepts back into it.
- **The capital sim runs downstream of the verdict and never re-litigates it.** Read an equity
  curve *with* its crucible verdict, never as a fresh judgement of the edge.
- **Only the orchestrator may promote, and only through the gate.** It is the one layer that may
  act. A promotion path that skips the gate is a bug, not a convenience.
- **Seam 4 is the only backward edge.** `DeploymentLedger.current()` carries live params back to
  signal generation. Keep every other seam a pure forward handoff.

## Contributor rules

- **The public API is pinned.** `tests/test_public_api.py` snapshots the exported surface, and
  `docs/api-stability.md` is the promise behind it. A name that vanishes or is renamed is a
  breaking change, fix it with a deprecation shim, not by editing the snapshot. A new export is a
  new promise, so add it to the snapshot deliberately, not by accident.
- **Determinism is a feature.** Same input, same seed, same result. Randomized procedures take an
  explicit seed and must reproduce.
- **Docs and tests move with the code.** A change to a seam, a boundary, or the public surface
  updates the relevant doc in `docs/` in the same change.
