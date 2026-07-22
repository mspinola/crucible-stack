"""The crucible API this package actually requires.

Pinning a version is a claim; this checks it. The honest-N corrections are the reason
`optimize` exists, and the failure mode without them is a TypeError raised deep inside
`select()` at the end of a long sweep, which is an expensive place to learn about a
version mismatch.

Worth being explicit about the history: published crucible 0.2.0 has
`deflated_sharpe(trial_sharpes, *, returns)` with no way to say how many variants were
tried, so it derives N from the number of scores. That is exactly the defect that made
`honest_n` decorative -- the ledger never reached the correction -- and it is not a
hypothetical, it shipped. If this package is ever installed against such a crucible,
these fail immediately and say why.
"""
import inspect

import pytest


def test_deflated_sharpe_accepts_the_honest_trial_count():
    from crucible.validation.pbo import deflated_sharpe
    params = inspect.signature(deflated_sharpe).parameters
    assert "n_trials" in params, (
        "the installed crucible predates the honest-N fix (crucible #95). Without "
        "n_trials, deflated_sharpe derives N from the number of SCORED configs, so the "
        "SearchSpaceLog never reaches the correction and the deflation is too weak. "
        "Install crucible from main, or >= 0.3.0 once released.")


def test_sidak_accepts_a_search_space_log_not_only_a_count():
    from crucible.validation import SearchSpaceLog
    from crucible.validation.permutation import sidak_correction
    log = SearchSpaceLog("compat-check")
    for fast in (5, 10, 20):
        log.record({"fast": fast}, score=0.1)     # an empty ledger has 0 variants, and
    assert log.n_variants == 3                    # 0 is rejected for good reason
    try:
        got = sidak_correction(0.01, log)
    except TypeError as exc:
        pytest.fail(
            f"the installed crucible predates crucible #96 ({exc}). sidak_correction must "
            "accept the ledger directly so the corrected p-value cannot be computed "
            "against a count someone retyped by hand.")
    assert 0.0 <= got <= 1.0
