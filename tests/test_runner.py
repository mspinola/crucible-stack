"""runner — one turn of the loop, and the cron entrypoint over it."""
import ast
import inspect
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from crucible_stack.optimize import Selection
from crucible_stack.orchestrate import DeploymentLedger, DriftTrigger, ScheduleTrigger, any_of
from crucible_stack.orchestrate.drift import envelope_from_r
from crucible_stack.orchestrate.runner import CycleResult, Reoptimization, missed_windows, run_cycle


def _sel(trustworthy=True, **kw):
    base = dict(config_id="cfg_1", params={"fast": 10, "slow": 30}, honest_n=12,
                trial_sharpe=1.2, pbo=0.1, deflated_sharpe=0.97, reality=None,
                trustworthy=trustworthy, reasons=("pbo ok (0.10 vs <= 0.5)",))
    base.update(kw)
    return Selection(**base)


def _env(seed=3):
    return envelope_from_r(np.random.default_rng(seed).normal(0.3, 1.0, 40),
                           n_sims=200, seed=1)


def _reopt(trustworthy=True, envelope=None, **kw):
    return lambda: Reoptimization(
        selection=_sel(trustworthy, **kw),
        fit_window=(pd.Timestamp("2015-01-01"), pd.Timestamp("2025-12-31")),
        envelope=envelope if envelope is not None else _env(),
        equity_ref="run-42",
    )


NOW = datetime(2026, 1, 1)


def _cycle(ledger, *, trustworthy=True, trigger=None, realized_r=(), cadence=None,
           reoptimize=None, book="book_a"):
    return run_cycle(
        book=book, ledger=ledger,
        trigger=trigger if trigger is not None else ScheduleTrigger(cadence=1),
        reoptimize=reoptimize if reoptimize is not None else _reopt(trustworthy),
        realized_r=realized_r, now=NOW, cadence=cadence)


# --- the no-op path -------------------------------------------------------------------

def _with_incumbent(**kw):
    """A ledger with something already live — required before any cycle can be quiet,
    since cold start fires under every policy (nothing live means nothing to preserve)."""
    led = DeploymentLedger()
    _cycle(led, **kw)
    assert led.current("book_a") is not None
    return led


def test_a_cycle_that_does_not_fire_records_nothing():
    led = _with_incumbent()
    before = len(led)
    res = _cycle(led, trigger=ScheduleTrigger(cadence=99), realized_r=np.zeros(3))
    assert res.fired is False and res.entry is None
    assert len(led) == before, "a non-event was written to the ledger"


def test_a_quiet_cycle_does_not_burn_a_search():
    """Re-optimizing costs variants in the SearchSpaceLog, so it must not run speculatively."""
    led = _with_incumbent()
    calls = []

    def reoptimize():
        calls.append(1)
        return _reopt()()

    _cycle(led, trigger=ScheduleTrigger(cadence=99), realized_r=np.zeros(3),
           reoptimize=reoptimize)
    assert calls == []


def test_cold_start_fires_even_on_a_long_cadence():
    """The counterpart: with nothing live there is nothing to preserve, so the loop must
    not sit idle waiting out a cadence it has never satisfied."""
    calls = []

    def reoptimize():
        calls.append(1)
        return _reopt()()

    res = _cycle(DeploymentLedger(), trigger=ScheduleTrigger(cadence=99),
                 realized_r=np.zeros(3), reoptimize=reoptimize)
    assert res.fired is True and calls == [1]


# --- promote / hold / halt ------------------------------------------------------------

def test_a_trustworthy_candidate_is_promoted_and_becomes_live():
    led = DeploymentLedger()
    res = _cycle(led, trustworthy=True)
    assert res.deployed is True and res.entry.action == "promote"
    assert led.current("book_a").params == {"fast": 10, "slow": 30}


def test_an_untrustworthy_candidate_holds_the_incumbent():
    led = _with_incumbent(reoptimize=_reopt(True, **{"params": {"v": 1}}))
    res = _cycle(led, reoptimize=_reopt(False, **{"params": {"v": 2}}),
                 realized_r=np.zeros(3))          # elapsed>0 so the cadence can be met
    assert res.entry.action == "hold" and res.deployed is False
    assert led.current("book_a").params == {"v": 1}, "the refused candidate went live"


def test_an_untrustworthy_candidate_on_cold_start_halts():
    led = DeploymentLedger()
    res = _cycle(led, trustworthy=False)
    assert res.entry.action == "halt"
    assert led.current("book_a") is None


# --- a refusal provisions nothing -----------------------------------------------------

def test_a_refusal_carries_no_envelope():
    """An envelope is the authorization artifact of a promotion; a refusal authorized
    nothing, and a stored rejected envelope is a re-baselining hazard."""
    led = DeploymentLedger()
    res = _cycle(led, trustworthy=False)
    assert res.entry.envelope is None and res.entry.equity_ref is None


def test_a_refusal_still_records_the_candidate_for_audit():
    led = DeploymentLedger()
    res = _cycle(led, trustworthy=False, reoptimize=_reopt(False, **{"params": {"v": 9}}))
    assert res.entry.params == {"v": 9}
    assert res.entry.verdict == "NOT TRUSTWORTHY" and res.entry.trustworthy is False


def test_a_hold_leaves_the_original_frozen_envelope_in_force():
    """Across cycles the drift baseline must remain the one the promotion provisioned."""
    original = _env(seed=7)
    led = _with_incumbent(reoptimize=_reopt(True, envelope=original))
    res = _cycle(led, reoptimize=_reopt(False, envelope=_env(seed=99)),
                 realized_r=np.zeros(3))
    assert res.entry is not None and res.entry.action == "hold", \
        "the refusal never happened, so this asserts nothing"

    live = led.current("book_a").envelope
    assert np.array_equal(live.cum_r, original.cum_r), "the baseline moved on a refusal"
    assert not np.array_equal(_env(seed=99).cum_r, original.cum_r), \
        "the two envelopes are identical, so this could not detect a moved baseline"


# --- missed windows -------------------------------------------------------------------

@pytest.mark.parametrize("elapsed,cadence,expected", [
    (0, 6, 0), (5, 6, 0), (6, 6, 0),        # on time
    (12, 6, 1), (13, 6, 1), (18, 6, 2),     # skipped
    (99, None, 0), (99, 0, 0),              # no cadence to compare against
])
def test_missed_window_arithmetic(elapsed, cadence, expected):
    assert missed_windows(elapsed, cadence) == expected


def test_a_skipped_window_is_reported_loudly():
    led = DeploymentLedger()
    res = _cycle(led, realized_r=np.zeros(13), cadence=6)
    assert res.missed == 1
    assert any("skipped" in r for r in res.reasons)
    assert any("WARNING" in r for r in res.entry.reasons), "the warning must reach the ledger"


# --- provenance -----------------------------------------------------------------------

def test_the_entry_names_every_trigger_that_fired():
    led = DeploymentLedger()
    _cycle(led)                                                  # seed an incumbent
    res = _cycle(led, trigger=any_of(ScheduleTrigger(cadence=1), DriftTrigger()),
                 realized_r=np.full(12, -3.0))
    assert set(res.entry.trigger.split("+")) == {"schedule", "drift"}


def test_reasons_carry_both_the_trigger_and_the_gate():
    led = DeploymentLedger()
    res = _cycle(led, trustworthy=False)
    assert any("schedule" in r for r in res.entry.reasons)
    assert any(r.startswith("gate:") for r in res.entry.reasons)


def test_result_is_reportable():
    assert isinstance(_cycle(DeploymentLedger()), CycleResult)


# --- the clock stays at the edge -------------------------------------------------------

def test_the_runner_never_reads_the_system_clock():
    import crucible_stack.orchestrate.runner as mod
    for node in ast.walk(ast.parse(inspect.getsource(mod))):
        if isinstance(node, ast.Attribute) and node.attr in {"now", "utcnow", "today"}:
            pytest.fail(f"runner reads the clock via .{node.attr}()")
    assert "now" in inspect.signature(run_cycle).parameters


# --- the cron entrypoint ---------------------------------------------------------------

class _Book:
    """Test wiring resolved by dotted path, exactly as a real book would be."""

    @staticmethod
    def realized_r_since(since=None, params=None):
        return np.zeros(0) if params is None else np.zeros(3)

    @staticmethod
    def reoptimize():
        return _reopt(trustworthy=False)()


def build():
    return _Book()


def test_cli_runs_a_cycle_and_reports_halt(tmp_path, capsys):
    from crucible_stack.orchestrate.__main__ import EXIT_HALT, main
    code = main(["--book", "book_a", "--ledger", str(tmp_path / "l.jsonl"),
                 "--book-factory", "tests.test_runner:build", "--cadence", "6"])
    assert code == EXIT_HALT                    # untrustworthy + nothing live == unsafe
    assert "HALT" in capsys.readouterr().out


def test_cli_dry_run_writes_nothing(tmp_path):
    from crucible_stack.orchestrate.__main__ import main
    p = tmp_path / "l.jsonl"
    main(["--book", "book_a", "--ledger", str(p),
          "--book-factory", "tests.test_runner:build", "--dry-run"])
    assert not p.exists()


def test_cli_reports_a_bad_factory_instead_of_crashing(tmp_path, capsys):
    from crucible_stack.orchestrate.__main__ import EXIT_ERROR, main
    code = main(["--book", "book_a", "--ledger", str(tmp_path / "l.jsonl"),
                 "--book-factory", "not_a_dotted_path"])
    assert code == EXIT_ERROR
    assert "FAILED" in capsys.readouterr().err


# ── --status: what is live, without a book factory or a data store ──────────────

def _entry(action="promote", *, day=1, trustworthy=True, **kw):
    from crucible_stack.orchestrate import DeploymentEntry
    base = dict(book="book_a", timestamp=datetime(2026, 1, day), action=action,
                trigger="schedule", params={"fast": 10}, verdict="TRUSTWORTHY",
                trustworthy=trustworthy, reasons=("pbo ok",), honest_n=12)
    base.update(kw)
    return DeploymentEntry(**base)


def _seeded_ledger(tmp_path, day=1):
    from crucible_stack.orchestrate import DeploymentLedger
    p = str(tmp_path / "l.jsonl")
    led = DeploymentLedger(p)
    _cycle(led, trustworthy=True)                      # a promotion to report on
    return p


def test_status_on_an_empty_ledger_says_nothing_is_live(tmp_path, capsys):
    from crucible_stack.orchestrate.__main__ import EXIT_OK, main
    code = main(["--status", "--book", "book_a", "--ledger", str(tmp_path / "none.jsonl")])
    out = capsys.readouterr().out
    assert code == EXIT_OK and "nothing" in out and "flat" in out


def test_status_reports_the_live_params_and_needs_no_book_factory(tmp_path, capsys):
    from crucible_stack.orchestrate.__main__ import EXIT_OK, main
    p = _seeded_ledger(tmp_path)
    code = main(["--status", "--book", "book_a", "--ledger", p])   # no --book-factory
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "LIVE:" in out and "TRUSTWORTHY" in out and "promote=1" in out


def test_status_flags_an_overdue_incumbent(tmp_path, capsys):
    from crucible_stack.orchestrate import DeploymentLedger
    from crucible_stack.orchestrate.__main__ import main
    p = str(tmp_path / "l.jsonl")
    led = DeploymentLedger(p)
    led.record(_entry(day=1))                          # promoted 2026-01-01, long ago
    main(["--status", "--book", "book_a", "--ledger", p, "--cadence", "1"])
    assert "OVERDUE" in capsys.readouterr().out


def test_status_surfaces_the_coherence_caveat(tmp_path, capsys):
    from crucible_stack.orchestrate import DeploymentLedger
    from crucible_stack.orchestrate.__main__ import main
    p = str(tmp_path / "l.jsonl")
    DeploymentLedger(p).record(_entry(
        reasons=("objective 'expectancy' picked X, but ... are NOT the same metric; ...",)))
    main(["--status", "--book", "book_a", "--ledger", p])
    assert "DIFFERENT metrics" in capsys.readouterr().out


def test_a_cycle_still_requires_a_book_factory(tmp_path, capsys):
    from crucible_stack.orchestrate.__main__ import EXIT_ERROR, main
    code = main(["--book", "book_a", "--ledger", str(tmp_path / "l.jsonl")])
    assert code == EXIT_ERROR
    assert "--book-factory is required" in capsys.readouterr().err


# ── --quiet: speak only when something happened ────────────────────────────────

def test_quiet_says_nothing_when_the_cycle_does_not_fire(tmp_path, capsys):
    """cron mails only when a job produces output, so silence here IS the feature."""
    from crucible_stack.orchestrate import DeploymentLedger
    from crucible_stack.orchestrate.__main__ import EXIT_OK, main
    p = str(tmp_path / "l.jsonl")
    # an incumbent WITH an envelope: cold start fires under every policy, and an
    # incumbent lacking an envelope makes DriftTrigger fire loudly (it fails open)
    DeploymentLedger(p).record(_entry(envelope=_env()))
    capsys.readouterr()
    code = main(["--book", "book_a", "--ledger", p, "--book-factory",
                 "tests.test_runner:build_quiet", "--cadence", "99", "--quiet"])
    assert code == EXIT_OK
    assert capsys.readouterr().out == "", "a non-event must not wake anyone up"


def test_quiet_still_speaks_when_there_is_a_decision(tmp_path, capsys):
    from crucible_stack.orchestrate.__main__ import EXIT_HALT, main
    code = main(["--book", "book_a", "--ledger", str(tmp_path / "l.jsonl"),
                 "--book-factory", "tests.test_runner:build", "--quiet"])
    assert code == EXIT_HALT
    assert "HALT" in capsys.readouterr().out


class _QuietBook:
    """An incumbent already live and nothing elapsed — the steady-state no-op."""
    @staticmethod
    def realized_r_since(since=None, params=None):
        return np.zeros(0)

    @staticmethod
    def reoptimize():
        return _reopt(trustworthy=True)()


def build_quiet():
    return _QuietBook()
