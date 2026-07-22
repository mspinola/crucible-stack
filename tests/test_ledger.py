"""ledger — Seam 4. The audit trail, so its guarantees are the point, not its convenience."""
import ast
import inspect
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from crucible_stack.orchestrate import (
    DeploymentEntry, DeploymentLedger, check_drift, envelope_from_r,
)


def _env(n=40, seed=3):
    return envelope_from_r(np.random.default_rng(seed).normal(0.3, 1.0, n),
                           n_sims=200, seed=1)


def _entry(book="book_a", action="promote", *, day=1, trustworthy=None, **kw):
    if trustworthy is None:
        trustworthy = action == "promote"
    base = dict(
        book=book, timestamp=datetime(2026, 1, day), action=action, trigger="schedule",
        params={"fast": 10, "slow": 30}, verdict="TRUSTWORTHY" if trustworthy else "NOT",
        trustworthy=trustworthy, reasons=("pbo ok (0.12)",), honest_n=12,
        fit_window=(pd.Timestamp("2015-01-01"), pd.Timestamp("2025-12-31")),
    )
    base.update(kw)
    return DeploymentEntry(**base)


# --- multi-book ----------------------------------------------------------------------

def test_books_do_not_interfere():
    led = DeploymentLedger()
    led.record(_entry("book_a", day=1, params={"fast": 10}))
    led.record(_entry("xyz1", day=2, params={"fast": 99}))
    assert led.current("book_a").params["fast"] == 10
    assert led.current("xyz1").params["fast"] == 99
    assert led.books == ("book_a", "xyz1")


def test_an_unknown_book_has_nothing_live():
    led = DeploymentLedger()
    led.record(_entry("book_a"))
    assert led.current("abc") is None


def test_history_filters_by_book_and_action():
    led = DeploymentLedger()
    led.record(_entry("book_a", "promote", day=1))
    led.record(_entry("book_a", "hold", day=2))
    led.record(_entry("xyz1", "halt", day=3))
    assert len(led.history()) == 3
    assert len(led.history(book="book_a")) == 2
    assert len(led.history(book="book_a", action="hold")) == 1
    with pytest.raises(ValueError, match="action must be one of"):
        led.history(action="nonsense")


def test_every_entry_needs_a_book_key():
    with pytest.raises(ValueError, match="needs a book key"):
        _entry(book="")


# --- current() is derived ------------------------------------------------------------

def test_current_is_the_most_recent_promotion():
    led = DeploymentLedger()
    led.record(_entry(day=1, params={"v": 1}))
    led.record(_entry(day=2, params={"v": 2}))
    assert led.current("book_a").params["v"] == 2


def test_holds_and_halts_do_not_change_what_is_live():
    led = DeploymentLedger()
    led.record(_entry(day=1, params={"v": 1}))
    led.record(_entry(action="hold", day=2, params={"v": 999}))
    led.record(_entry(action="halt", day=3, params={"v": 888}))
    assert led.current("book_a").params["v"] == 1, "a refusal must not become the live set"


def test_nothing_is_live_before_the_first_promotion():
    led = DeploymentLedger()
    led.record(_entry(action="halt", day=1))
    assert led.current("book_a") is None          # cold start: the gate must not 'hold' nothing


def test_refusals_are_recorded_so_the_incumbent_is_explainable():
    led = DeploymentLedger()
    led.record(_entry(day=1))
    led.record(_entry(action="hold", day=2, reasons=("gate: REFUSED - not trustworthy",)))
    holds = led.history(book="book_a", action="hold")
    assert len(holds) == 1 and "REFUSED" in holds[0].reasons[0]


# --- the gate invariant reaches into the record --------------------------------------

def test_an_untrustworthy_promotion_cannot_be_recorded():
    with pytest.raises(ValueError, match="untrustworthy"):
        _entry(action="promote", trustworthy=False)


def test_untrustworthy_hold_and_halt_are_fine():
    for action in ("hold", "halt"):
        assert _entry(action=action, trustworthy=False).action == action


def test_unknown_action_is_rejected():
    with pytest.raises(ValueError, match="action must be one of"):
        _entry(action="deploy")


# --- append-only ---------------------------------------------------------------------

def test_there_is_no_way_to_edit_or_delete_history():
    api = {m for m in dir(DeploymentLedger) if not m.startswith("_")}
    for mutator in ("update", "delete", "remove", "pop", "clear", "edit", "amend", "rewrite"):
        assert not any(mutator in m for m in api), f"ledger grew a {mutator} method"
    assert "record" in api


def test_entries_are_frozen():
    e = _entry()
    with pytest.raises(Exception):
        e.action = "hold"


# --- persistence: the envelope must survive a restart --------------------------------

def test_in_memory_by_default_writes_nothing(tmp_path):
    led = DeploymentLedger()
    led.record(_entry())
    assert list(tmp_path.iterdir()) == []


def test_entries_reload_from_disk(tmp_path):
    p = str(tmp_path / "ledger.jsonl")
    DeploymentLedger(p).record(_entry(params={"fast": 10}))
    reloaded = DeploymentLedger(p)
    assert len(reloaded) == 1
    assert reloaded.current("book_a").params["fast"] == 10


def test_a_restart_does_not_force_rebuilding_the_envelope(tmp_path):
    """THE persistence guarantee. If the frozen envelope could not round-trip, a restart
    would force the loop to rebuild one from current data — re-baselining by the back
    door, and invisible because everything else would look fine."""
    p = str(tmp_path / "ledger.jsonl")
    env = _env()
    DeploymentLedger(p).record(_entry(envelope=env))

    live = np.full(12, -2.0)
    before = check_drift(env, live)
    after = check_drift(DeploymentLedger(p).current("book_a").envelope, live)

    assert before.drifted is after.drifted is True
    assert after.cum_r_floor == pytest.approx(before.cum_r_floor)
    assert after.max_dd_floor == pytest.approx(before.max_dd_floor)
    assert after.reasons == before.reasons


def test_envelope_round_trips_exactly(tmp_path):
    p = str(tmp_path / "l.jsonl")
    env = _env()
    DeploymentLedger(p).record(_entry(envelope=env))
    back = DeploymentLedger(p).current("book_a").envelope
    assert np.array_equal(back.cum_r, env.cum_r)
    assert np.array_equal(back.max_dd, env.max_dd)
    assert back.levels == env.levels


def test_numpy_params_survive_the_json_round_trip(tmp_path):
    p = str(tmp_path / "l.jsonl")
    DeploymentLedger(p).record(_entry(params={"fast": np.int64(10), "thr": np.float64(0.5)}))
    got = DeploymentLedger(p).current("book_a").params
    assert got == {"fast": 10, "thr": 0.5}


def test_fit_window_round_trips(tmp_path):
    p = str(tmp_path / "l.jsonl")
    DeploymentLedger(p).record(_entry())
    assert DeploymentLedger(p).current("book_a").fit_window[1] == pd.Timestamp("2025-12-31")


def test_a_corrupt_line_is_an_error_not_a_silent_skip(tmp_path):
    p = tmp_path / "l.jsonl"
    DeploymentLedger(str(p)).record(_entry())
    p.write_text(p.read_text() + "{not json\n")
    with pytest.raises(ValueError, match="not a readable ledger entry"):
        DeploymentLedger(str(p))


def test_appending_does_not_rewrite_prior_lines(tmp_path):
    p = tmp_path / "l.jsonl"
    led = DeploymentLedger(str(p))
    led.record(_entry(day=1))
    first = p.read_text()
    led.record(_entry(day=2))
    assert p.read_text().startswith(first), "earlier lines were rewritten"


# --- no hidden clock -----------------------------------------------------------------

def test_the_ledger_never_reads_the_system_clock():
    """Asserted on the syntax tree, not the text — the docstring mentions datetime.now()
    precisely to say it is not called."""
    import crucible_stack.orchestrate.ledger as mod
    banned = {"now", "utcnow", "today", "monotonic", "perf_counter"}
    for node in ast.walk(ast.parse(inspect.getsource(mod))):
        if isinstance(node, ast.Attribute) and node.attr in banned:
            pytest.fail(f"ledger reads the clock via .{node.attr}()")


def test_incumbent_age_requires_an_explicit_now():
    led = DeploymentLedger()
    led.record(_entry(day=1))
    assert "now" in inspect.signature(led.incumbent_age).parameters
    age = led.incumbent_age("book_a", datetime(2026, 1, 8))
    assert age == pd.Timedelta(days=7)


def test_incumbent_age_is_none_when_nothing_is_live():
    assert DeploymentLedger().incumbent_age("book_a", datetime(2026, 1, 1)) is None
