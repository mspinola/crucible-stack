"""trigger — the re-optimization cadence, and the substrate seam it doubles as."""
import inspect

import numpy as np
import pytest

from crucible_stack.orchestrate import (
    DriftTrigger,
    ScheduleTrigger,
    Trigger,
    TriggerContext,
    TriggerDecision,
    any_of,
    envelope_from_r,
)


def _healthy_r(n=60, seed=3):
    return np.random.default_rng(seed).normal(0.3, 1.0, n)


def _env(n=60, seed=3):
    return envelope_from_r(_healthy_r(n, seed), n_sims=400, seed=1)


def _ctx(r=(), **kw):
    kw.setdefault("envelope", _env())
    return TriggerContext(realized_r=np.asarray(r, dtype=float), **kw)


# --- schedule ------------------------------------------------------------------------

def test_schedule_waits_until_the_cadence_elapses():
    t = ScheduleTrigger(cadence=6)
    assert t(_ctx(np.zeros(5))).fired is False
    assert t(_ctx(np.zeros(6))).fired is True


def test_schedule_reports_progress_even_when_it_does_not_fire():
    d = ScheduleTrigger(cadence=6)(_ctx(np.zeros(2)))
    assert d.fired is False and d.sources == ()
    assert "2 of 6" in d.reasons[0]


def test_schedule_rejects_a_nonsense_cadence():
    with pytest.raises(ValueError, match="cadence must be"):
        ScheduleTrigger(cadence=0)


# --- drift ---------------------------------------------------------------------------

def test_drift_fires_when_the_book_leaves_its_envelope():
    d = DriftTrigger()(_ctx(np.full(12, -2.0)))
    assert d.fired is True and d.sources == ("drift",)


def test_drift_stays_quiet_while_the_book_behaves():
    assert DriftTrigger()(_ctx(_healthy_r(20, seed=11))).fired is False


def test_drift_never_builds_an_envelope_it_was_not_given():
    """Re-baselining protection reaches through the trigger too."""
    src = inspect.getsource(DriftTrigger)
    assert "envelope_from_r" not in src and "build_envelope" not in src


def test_drift_with_no_envelope_fires_loudly_rather_than_trading_blind():
    d = DriftTrigger()(_ctx(np.zeros(5), envelope=None))
    assert d.fired is True
    assert "cannot be assessed" in d.reasons[0]


def test_drift_does_not_fire_before_any_live_periods():
    assert DriftTrigger()(_ctx([])).fired is False


# --- cold start ----------------------------------------------------------------------

def test_every_policy_is_due_when_nothing_is_live():
    for t in (ScheduleTrigger(cadence=99), DriftTrigger(), any_of(ScheduleTrigger(99))):
        d = t(_ctx(np.zeros(0), has_incumbent=False))
        assert d.fired is True, f"{t} failed to fire at cold start"
        assert any("no incumbent" in r for r in d.reasons)


# --- hybrid --------------------------------------------------------------------------

def test_hybrid_fires_when_either_policy_does():
    hybrid = any_of(ScheduleTrigger(cadence=99), DriftTrigger())
    assert hybrid(_ctx(np.full(12, -2.0))).fired is True          # drift only
    hybrid2 = any_of(ScheduleTrigger(cadence=2), DriftTrigger())
    assert hybrid2(_ctx(_healthy_r(20, seed=11))).fired is True   # schedule only


def test_hybrid_records_every_source_that_fired_not_just_the_first():
    d = any_of(ScheduleTrigger(cadence=2), DriftTrigger())(_ctx(np.full(12, -2.0)))
    assert set(d.sources) == {"schedule", "drift"}


def test_hybrid_keeps_reasons_from_policies_that_did_not_fire():
    d = any_of(ScheduleTrigger(cadence=99), DriftTrigger())(_ctx(np.full(12, -2.0)))
    assert d.sources == ("drift",)
    assert any("of 99 periods" in r for r in d.reasons), "schedule's reasoning was dropped"


def test_hybrid_needs_at_least_one_policy():
    with pytest.raises(ValueError, match="at least one trigger"):
        any_of()


# --- the seam ------------------------------------------------------------------------

def test_implementations_satisfy_the_protocol():
    for t in (ScheduleTrigger(cadence=6), DriftTrigger(), any_of(DriftTrigger())):
        assert isinstance(t, Trigger)


def test_triggers_know_nothing_about_the_substrate():
    """The point of the seam: no scheduler/engine/clock dependency reaches a trigger.

    Asserted on the import surface rather than the text, so the docstring stays free to
    *discuss* cron and workflow engines without tripping the guard.
    """
    import ast

    import crucible_stack.orchestrate.trigger as mod

    imported = set()
    for node in ast.walk(ast.parse(inspect.getsource(mod))):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    banned = {"croniter", "prefect", "dagster", "temporal", "airflow", "schedule",
              "time", "datetime", "asyncio", "subprocess", "os", "sched"}
    assert not (imported & banned), f"substrate dependency leaked: {imported & banned}"
    assert imported <= {"__future__", "dataclasses", "typing", "numpy", "crucible_stack"}, \
        f"unexpected dependency in the seam: {imported}"


def test_context_derives_elapsed_rather_than_tracking_it():
    params = set(inspect.signature(TriggerContext).parameters)
    assert "elapsed" not in params, "elapsed must be derived, not a second source of truth"
    assert _ctx(np.zeros(7)).elapsed == 7


def test_decision_is_reportable():
    d = ScheduleTrigger(cadence=1)(_ctx(np.zeros(3)))
    assert isinstance(d, TriggerDecision)
    assert "+".join(d.sources) == "schedule"        # the form Seam 4 stores
