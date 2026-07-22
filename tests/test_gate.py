"""gate — the honesty gate must never let an untrustworthy verdict reach a live book.

These are guard tests, not coverage tests. ADR-0003 calls the gate "the first thing to
build and the last thing to break"; each test below pins one way it could silently break.
"""
import inspect
from types import SimpleNamespace

import numpy as np
import pandas as pd

from crucible.strategies import ma_cross

from crucible_stack.optimize import sweep, select, Selection
from crucible_stack.orchestrate import GateDecision, evaluate, is_promotable


def _sel(trustworthy, reasons=("pbo ok (0.10 vs <= 0.5)", "deflated_sharpe ok (97% vs >= 95%)")):
    """A Selection with a chosen verdict; the other fields are inert for the gate."""
    return Selection(
        config_id="cfg_1", params={"fast": 5, "slow": 20}, honest_n=4,
        trial_sharpe=1.2, pbo=0.10, deflated_sharpe=0.97, reality=None,
        trustworthy=trustworthy, reasons=reasons,
    )


def _ohlc(n=520, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2015-01-01", periods=n, freq="B")
    close = 100 + np.cumsum(rng.normal(0.05, 1.0, n))
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    open_ = close - rng.normal(0, 0.5, n)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close}, index=idx)


# --- the core property: trustworthy is the only path to a deployment ----------------

def test_trustworthy_selection_promotes():
    d = evaluate(_sel(True), has_incumbent=True)
    assert isinstance(d, GateDecision)
    assert d.action == "promote" and d.deploys is True


def test_trustworthy_promotes_on_cold_start_too():
    # No incumbent is not a reason to refuse a good verdict - it is the first deployment.
    assert evaluate(_sel(True), has_incumbent=False).action == "promote"


def test_untrustworthy_holds_the_incumbent_and_cannot_deploy():
    d = evaluate(_sel(False), has_incumbent=True)
    assert d.action == "hold" and d.deploys is False


def test_untrustworthy_on_cold_start_halts_rather_than_holding_nothing():
    # There is no incumbent to hold, so "hold" would be an unhandled state, not a no-op.
    d = evaluate(_sel(False), has_incumbent=False)
    assert d.action == "halt" and d.deploys is False


# --- failing closed ------------------------------------------------------------------

def test_no_selection_is_refused_not_promoted():
    assert is_promotable(None) is False
    assert evaluate(None, has_incumbent=True).action == "hold"
    assert evaluate(None, has_incumbent=False).action == "halt"


def test_truthy_non_bool_verdict_cannot_authorize_a_deployment():
    # A Mock, a stub, or a stray non-empty string must not read as authorization.
    for spoof in ("False", 1, [0], object(), SimpleNamespace()):
        fake = SimpleNamespace(trustworthy=spoof, reasons=())
        assert is_promotable(fake) is False, f"{spoof!r} was treated as trustworthy"
        assert evaluate(fake, has_incumbent=True).deploys is False


def test_missing_trustworthy_attribute_is_refused():
    assert is_promotable(SimpleNamespace(reasons=())) is False


# --- the gate must stay un-bypassable ------------------------------------------------

def test_gate_exposes_no_override_parameter():
    """Enforced structurally: adding a bypass kwarg should fail this test, not review."""
    banned = ("force", "override", "bypass", "skip", "ignore", "unsafe", "allow")
    for fn in (evaluate, is_promotable):
        params = set(inspect.signature(fn).parameters)
        assert not {p for p in params if any(b in p.lower() for b in banned)}, \
            f"{fn.__name__} grew a bypass parameter"
    assert set(inspect.signature(evaluate).parameters) == {"selection", "has_incumbent"}


# --- the decision must be self-contained evidence ------------------------------------

def test_reasons_carry_the_selection_verbatim_beneath_the_gate_line():
    sel = _sel(False, reasons=("pbo too high (0.71 vs <= 0.5)",))
    d = evaluate(sel, has_incumbent=True)
    assert d.reasons[0].startswith("gate: REFUSED")
    assert sel.reasons[0] in d.reasons          # audit needs no re-run of the optimizer


def test_refusal_says_which_degenerate_case_it_was():
    assert "no selection" in evaluate(None, has_incumbent=True).reasons[0]
    assert "NOT trustworthy" in evaluate(_sel(False), has_incumbent=True).reasons[0]


# --- end to end against the real pipeline --------------------------------------------

def test_random_walk_sweep_is_refused_by_the_gate():
    """The honest spine, wired: a no-edge search must not reach a live book."""
    tm = sweep(_ohlc(), ma_cross, {"fast": [5, 10], "slow": [20, 30]}, timeout=15)
    s = select(tm, pbo_blocks=4)
    assert s.trustworthy is False                       # precondition: no real edge
    d = evaluate(s, has_incumbent=True)
    assert d.action == "hold" and d.deploys is False
    assert any("deflated_sharpe" in r for r in d.reasons)
