"""Prose must not carry research findings.

The gap this closes, stated plainly: `test_boundaries.py` reads the syntax tree, so it sees
imports, definitions and identifiers. A docstring is a string constant and a markdown file
is not code at all, and **every leak found while extracting this framework came through
prose**. Not one came through an import.

Three separate audits found them, all by a person reading rather than a test:

  * the runbooks reported which books cleared the gate, the winning configuration by name
    with its pbo and deflated-Sharpe numbers, and per-book equity ranges;
  * two test docstrings carried a scan result and real per-book measurements
    (trade counts, expectancy, Sharpe), present in every commit since the second one;
  * a config-schema comment named one strategy's parameters.

The distinction this file draws is **findings, not vocabulary**. A naive scan for strategy
names flags a dozen legitimate provenance notes ("moved out of `npf.validation` per
ADR-0004") and would be deleted within a week for crying wolf, which is how a guard dies.
What is banned is the shape of a *result*: money, measurements, private identifiers, and
verdicts attached to a named book. Generic framework prose says "the candidate cleared the
gate" all day and that is fine; "the trend+COT book cleared the gate" is not.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Names for somebody's actual books and configs. Not "npf" or "CMR" on their own, which
# appear in honest provenance notes about where this code came from.
BOOK_NAME = r"(?:cmr|npf|trend[-+ ]?cot|donchian|willco|gold[-_ ]?trend)"

PATTERNS = {
    "a currency amount":
        # This package computes equity curves but never reports one. A dollar figure in
        # prose is somebody's account, or somebody's backtest, and neither is framework
        # documentation. Code examples are exempt, see `_prose`.
        (re.compile(r"\$\s?[\d,]+(?:\.\d+)?\s*[kKmMbB]?\b"),
         "a framework that never sees an account must not quote one"),

    "a private strategy identifier":
        # Column and parameter names from a specific strategy's world.
        (re.compile(r"\b(comms_idx|lrg_idx|sml_idx|willco_\w+|index_oinorm|cot_arm|"
                    r"macro_neutral_line)\b", re.I),
         "names a specific strategy's columns or parameters"),

    "an R-multiple measurement":
        # "+0.405 R" is a result. "3R" as a unit in an explanation is not, hence the
        # requirement for a decimal and a sign.
        (re.compile(r"[+-]\d+\.\d+\s*R\b"),
         "reports a measured edge rather than explaining the unit"),

    "a config identifier":
        # The shape a selected configuration is printed in, e.g. `index_oinorm|regime=1`.
        (re.compile(r"\b\w+\|\w+=\S+"),
         "looks like a selected configuration, printed"),

    "a verdict about a named book":
        # Generic framework prose says "what clears the gate" constantly and must not fire.
        # A book NAME beside the verdict is the thing that makes it a finding.
        (re.compile(BOOK_NAME + r"[^.\n]{0,60}?(?:clears?|cleared|does not clear|failed?)\s+"
                    r"the gate|(?:clears?|cleared|does not clear)\s+the gate[^.\n]{0,60}?"
                    + BOOK_NAME + r"|" + BOOK_NAME + r"[^.\n]{0,40}?(?:has been|was)\s+promoted",
                    re.I),
         "states an outcome for a specific book"),
}

# Historical decision records. They carry a provenance header saying they were written in
# the strategy repo before the extraction, and rewriting them to match today would be
# falsifying the record rather than protecting anything.
EXCLUDED_DIRS = ("docs/adr/",)

# Deliberate exceptions. Each one must say WHY, because an undocumented exception is
# indistinguishable from a leak someone silenced.
ALLOWED = {
    "crucible_stack/engine/exits.py":
        "explains why the engine's default exit is no longer a COT mode. Naming the column "
        "it used to read is the whole point of the explanation, and the AST guard in "
        "test_boundaries.py separately proves no such column is read in code.",
    "crucible_stack/framework/config.py":
        "records that the schema once REQUIRED one strategy's parameters of every strategy. "
        "Naming them is what stops the mistake being repeated.",
    "tests/test_boundaries.py":
        "is a guard; its banned-word list must contain the banned words.",
    "tests/test_no_findings_in_prose.py":
        "is this guard. Its patterns and its regression corpus must contain the exact "
        "strings it exists to catch, including the real leaks replayed below.",
}


def _prose(path: pathlib.Path) -> str:
    """The text that makes claims.

    Fenced code blocks are stripped from markdown: `simulate_equity(starting_capital=100_000)`
    is an API example, not a report of what an account did. Python files are read whole,
    since a finding is as damaging in a comment as in a docstring.
    """
    text = path.read_text()
    if path.suffix == ".md":
        return re.sub(r"```.*?```", "", text, flags=re.S)
    return text


def _files():
    out = []
    for p in sorted(list(ROOT.rglob("*.md")) + list(ROOT.rglob("*.py"))):
        rel = p.relative_to(ROOT).as_posix()
        if ".git/" in rel or rel.startswith(EXCLUDED_DIRS) or rel in ALLOWED:
            continue
        out.append(p)
    return out


def test_there_is_something_to_check():
    """A scanner that stops finding files passes forever."""
    files = _files()
    assert len(files) >= 25, f"prose guard stopped seeing the repo: {len(files)} files"
    assert any(f.suffix == ".md" for f in files), "no markdown in scope"
    assert any(f.suffix == ".py" for f in files), "no python in scope"


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_findings_in_prose(path):
    rel = path.relative_to(ROOT).as_posix()
    body = _prose(path)
    problems = []
    for label, (rx, why) in PATTERNS.items():
        hits = sorted({m.group(0).strip() for m in rx.finditer(body)})
        if hits:
            problems.append(f"{label} ({why}): {hits[:3]}")
    assert not problems, (
        f"{rel} carries what looks like a research finding:\n  " + "\n  ".join(problems) +
        "\n\nThis repo is public and the strategies are not. Describe the situation the "
        "code exists for rather than reporting what a particular book did. If this is a "
        "genuine false positive, add the file to ALLOWED with a reason.")


def test_every_allowed_exception_still_exists():
    """An exception for a deleted file is a hole nobody can see."""
    for rel in ALLOWED:
        assert (ROOT / rel).exists(), f"ALLOWED names {rel}, which no longer exists"


def test_every_allowed_exception_carries_a_reason():
    for rel, why in ALLOWED.items():
        assert why and len(why) > 30, f"ALLOWED[{rel}] needs a real reason, not {why!r}"


# --- the guard is checked against the leaks that actually happened ----------------------

REAL_LEAKS = [
    # from the runbooks, caught during the docs split (ADR-0004 item 8)
    "The trend+COT book has been promoted, the first time the loop deployed anything.",
    "It selected index_oinorm|regime=1 on expectancy and cleared the gate.",
    "the single $300k curve hid a 5-95 terminal range of $69k to $1.34M",
    "No single-market CMR long book clears the gate.",
    # from the test docstrings, caught in the pre-publication history audit
    "its COT filter cuts trade count 3627 -> 932, which nearly doubles per-trade "
    "expectancy (+0.405 -> +0.706 R)",
    # from the config schema
    "ExitLogic required macro_neutral_line of every strategy",
]

GENUINE_FRAMEWORK_PROSE = [
    "the candidate cleared the gate and is now live.",
    "promotes only what clears the gate, holds the incumbent when it does not",
    "Both variants can clear the gate while disagreeing about which is best.",
    "R accumulates additively: 3R + 2R = 5R of risk units.",
    "Moved out of npf.validation per ADR-0004, it is engine machinery.",
    "pass `simulate_equity(log, starting_capital=100_000)` to size it",
]


@pytest.mark.parametrize("text", REAL_LEAKS)
def test_it_catches_the_leaks_that_actually_happened(text):
    """Every one of these was really in this repo, in prose, at some point today."""
    assert any(rx.search(text) for rx, _ in PATTERNS.values()), \
        f"the guard would not have caught a real leak: {text!r}"


@pytest.mark.parametrize("text", GENUINE_FRAMEWORK_PROSE)
def test_it_does_not_fire_on_ordinary_framework_prose(text):
    """The failure mode that kills a text guard. Twice in this project a scan fired on its
    own explanatory docstring, and a guard that cries wolf gets deleted rather than fixed."""
    firing = [n for n, (rx, _) in PATTERNS.items() if rx.search(text)]
    assert not firing, f"{firing} fired on legitimate prose: {text!r}"
