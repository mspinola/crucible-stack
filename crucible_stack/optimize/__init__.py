"""crucible_stack.optimize — the optimizer layer.

The optimizer is "all the arrows": it drives a strategy across a parameter search,
records every variant into a SearchSpaceLog, assembles the result as a TrialMatrix,
and hands that to crucible for the honest verdict. This package owns the TrialMatrix
seam (the optimizer -> crucible contract); crucible never imports it — it consumes
`TrialMatrix.returns` as a plain DataFrame.

See docs/design/seam-contracts.md (Seam 1) and docs/adr/ADR-0001 for the design.
"""
from crucible_stack.optimize.trial_matrix import TrialMatrix
from crucible_stack.optimize.sweep import sweep
from crucible_stack.optimize.select import select, Selection
from crucible_stack.optimize.simulators import rules_simulator

__all__ = ["TrialMatrix", "sweep", "select", "Selection", "rules_simulator"]
