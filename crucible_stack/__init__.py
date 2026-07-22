"""crucible-stack — a framework for defining, searching, sizing and deploying strategies.

`crucible` answers *is this edge real?* for one set of trades. `crucible-stack` is the
machinery around that verdict: how you **define** a strategy (interfaces, params schema),
**search** its parameter space honestly (Seam 1), **size** it into an account (Seam 3), and
**deploy** it as a re-optimization loop that re-earns its keep on a schedule (Seam 4).

The split this package exists to make possible: **the framework is public, the strategies
are not.** Nothing in here contains a strategy, a data store, or a market opinion. The
registries ship EMPTY — `STRATEGY_REGISTRY`, `EXIT_RULES` — and implementations register
themselves from wherever they live, which may be a private repo.

Dependency direction is one-way, as with every seam in this project:

    crucible  <-  crucible_stack  <-  your strategies

See ADR-0004 for the reasoning, and ADR-0003 for the deployment loop's design.
"""
__version__ = "0.1.0.dev0"
