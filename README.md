# crucible-stack

A framework for defining, searching, sizing and deploying trading strategies.

**It contains no strategies, and that is the point.**

[`crucible`](https://github.com/mspinola/crucible) answers one question: *is this edge real,
or did I find it by looking hard enough?* `crucible-stack` is the machinery around that
verdict, the parts every systematic strategy needs and nobody wants to write twice:

| Package | What it does | Seam |
|---|---|---|
| `framework` | how you **define** a strategy: the `RulesStrategy` interface, the params/config schema, the strategy registry, the Monte Carlo engine | n/a |
| `engine` | the rules simulator and a pluggable exit/stop registry | n/a |
| `optimize` | how you **search** a parameter space honestly: the trial matrix, the sweep, and a selection step that prices in how many variants you actually tried | 1 |
| `capital` | how you **size** an edge into an account: R-multiples to a currency equity curve, with bootstrap bands | 3 |
| `orchestrate` | how you **deploy** it: a gate, a drift monitor, a ledger, and triggers that make re-optimization a standing process rather than a memory | 4 |

## Why the strategies are missing

The split is deliberate. A strategy is somebody's edge and may be worth keeping private. The
framework around it is not an edge, it is plumbing, and plumbing kept private is just
plumbing you maintain alone.

So every place a strategy would go is a **registry that ships empty**:

```python
from crucible_stack.framework import register_strategy, RulesStrategy
from crucible_stack.engine.exits import register_exit, ExitRule

@register_strategy("my_gold_trend")          # lives in YOUR repo, public or not
class GoldTrend(RulesStrategy):
    ...
```

`crucible-stack` never imports your code. Your code imports it. That direction is enforced
by a test (`tests/test_boundaries.py`), not by good intentions, and the test is
mutation-checked so that it fails when the boundary actually breaks.

## The idea worth stealing

Most backtesting stacks stop at a backtest. This one is shaped around two ideas that are
easy to state and are usually skipped:

**1. The search is part of the result.** If you try 400 variants and report the best one's
Sharpe, you have reported the maximum of 400 draws, not an edge. `optimize` keeps a
`SearchSpaceLog` of how many variants were genuinely tried and feeds that count into the
correction (deflated Sharpe, PBO, White's Reality Check, Hansen's SPA). The honest
denominator is the whole point, and it is the number everybody rounds down.

**2. A deployed strategy is not a finished one.** `orchestrate` treats deployment as a loop
that has to keep re-earning its place: a gate that **fails closed** (no verdict, no
promotion), a drift monitor that compares the live book against the envelope frozen *at
promotion* rather than one recomputed today, and a ledger that remembers what was promoted
and when. See ADR-0003 for why each of those is shaped the way it is, and ADR-0004 for why
this repo exists at all.

## Install

```bash
pip install -e .            # requires Python 3.11+, and crucible >= 0.3.0
pytest -q
```

### If you install this editable, read this first

The natural way to work on a strategy is to install both this package and your own
repo editable, side by side. That is a good setup and it has one property worth
stating plainly, because it causes bugs that look like anything but their cause: an
editable install *is* the working tree. There is no copy. `import crucible_stack`
reads the checkout directly, in whatever state it is in at that moment.

Three consequences:

**A git operation here changes your strategy's behaviour, with no change to your
strategy.** Switch branches, stash, or rebase in this repo and your next `import`
picks it up. A test that passes, then fails against an untouched strategy tree, is
usually this rather than a flake.

**Version metadata is written at install time and never refreshed.** `pip list` will
report whatever `pyproject.toml` said when you installed, not what the code is now. A
constraint like `crucible-stack>=0.1.0` is satisfied by a checkout that could be
anywhere from that release to unreleased main. Do not read a version pin as evidence
that an API is present. Check the signature, or write a test that does. This package
does exactly that for its own dependency in `tests/test_crucible_compat.py`, because
the crucible API it needs shipped after the tagged release it nominally requires.

**Do not declare a dependency by URL to work around a stale release.** It is tempting
to pin `crucible-stack @ git+https://...` when you need something newer than the last
tag. It breaks any consumer that installs the same package from a local editable
checkout (pip cannot reconcile a URL requirement with a local one) and it blocks PyPI
upload. Pin normally and enforce the API with a test.

## Status

Early. The API is not yet stable and will change without ceremony until a stability policy
lands. Extracted from a working private strategy repo rather than designed in the abstract,
which shows in both directions: the seams are load-bearing and have been used in anger, and
the naming still carries some of its origin.
