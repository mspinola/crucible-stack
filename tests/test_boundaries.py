"""The line this package exists to hold.

`crucible-stack` is a framework for defining strategies that deliberately contains none.
That claim is only worth anything if it is checked, because the failure mode is silent:
one convenient import of a strategy helper, and a public framework has a private
dependency that nobody notices until the next person tries to install it.

ADR-0004 action item 9. This replaces npf's `test_orchestrate_boundaries.py`, which could
only express the weaker "reaches down, not sideways" rule while the toolchain still lived
inside npf. Here the rule is absolute: **zero** imports from any strategy repo.

The npf-side history is worth keeping in view. That earlier guard allowlisted
`npf.validation.portfolio_mc`, which was true when written and became an artifact of the
old layout after the MC engine moved. It then failed, correctly, the moment the imports
were repointed. A boundary test is a claim about the code as it is *now*, so these are
written to fail loudly rather than to keep passing.
"""
import ast
import pathlib
import subprocess
import sys

import pytest

PKG = pathlib.Path(__file__).resolve().parent.parent / "crucible_stack"

# Strategy repos and data stores. A framework may not import any of them.
FORBIDDEN_ROOTS = ("npf", "cotmetrics", "cotdata", "cmr", "npf_books")

# Everything the package is allowed to reach for. `crucible` is the layer below (verdicts);
# the rest is the scientific-Python floor. Anything else is a new dependency and should be
# a deliberate decision made in pyproject.toml, not discovered here.
ALLOWED_THIRD_PARTY = {"numpy", "pandas", "yaml", "pydantic", "crucible"}
STDLIB_OK = True  # stdlib is unrestricted; the per-module seam tests are stricter


def _modules():
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in str(p))


def _imports(path):
    """Every dotted module name imported by a source file, including function-local ones."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_there_is_something_to_check():
    """A guard that silently stops seeing its target passes forever."""
    assert len(_modules()) >= 20, f"boundary test stopped seeing the package: {len(_modules())}"


@pytest.mark.parametrize("path", _modules(), ids=lambda p: str(p.relative_to(PKG)))
def test_no_module_imports_a_strategy_or_a_data_store(path):
    for name in _imports(path):
        root = name.split(".")[0]
        assert root not in FORBIDDEN_ROOTS, (
            f"{path.relative_to(PKG)} imports {name!r}. This package describes how to "
            "define a strategy; it must not depend on one. If the code genuinely needs "
            "this, it belongs in the strategy repo, not here.")


@pytest.mark.parametrize("path", _modules(), ids=lambda p: str(p.relative_to(PKG)))
def test_no_module_grows_an_undeclared_dependency(path):
    import sysconfig
    stdlib = set(sys.stdlib_module_names)
    for name in _imports(path):
        root = name.split(".")[0]
        if root in stdlib or root == "crucible_stack":
            continue
        assert root in ALLOWED_THIRD_PARTY, (
            f"{path.relative_to(PKG)} imports {root!r}, which is not in pyproject.toml's "
            f"dependencies {sorted(ALLOWED_THIRD_PARTY)}. Declare it deliberately or drop it.")


def test_the_package_imports_with_no_strategy_and_no_data_store():
    """The property the whole split turns on, checked end to end.

    In a subprocess deliberately: an in-process check would be satisfied by modules another
    test already imported, which is how a guard comes to pass for the wrong reason.
    """
    code = (
        "import os; os.environ.pop('COTDATA_STORE', None);"
        "import crucible_stack.framework, crucible_stack.optimize,"
        " crucible_stack.capital, crucible_stack.orchestrate, crucible_stack.engine.simulator;"
        "import sys;"
        "print('LEAKED', sorted(m for m in sys.modules"
        " if m.split('.')[0] in ('npf','cotmetrics','cotdata','cmr')))"
    )
    env = {k: v for k, v in __import__("os").environ.items() if k != "COTDATA_STORE"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, f"package failed to import standalone:\n{out.stderr[-800:]}"
    assert "LEAKED []" in out.stdout, f"dragged in a strategy or data store: {out.stdout.strip()}"


def test_the_registries_ship_empty():
    """The mechanism is framework; the contents are not.

    Both registries are the same pattern: shipping a populated one would put a strategy
    (or a strategy's exit rule, which reads its columns) inside a package that promises
    neither. Asserted on the syntax tree rather than at runtime, because by the time a
    test imports the module another test may already have registered into it.
    """
    cases = [("crucible_stack/framework/registry.py", "STRATEGY_REGISTRY"),
             ("crucible_stack/engine/exits.py", "EXIT_RULES")]
    root = PKG.parent
    for rel, symbol in cases:
        tree = ast.parse((root / rel).read_text())
        for node in ast.walk(tree):
            target = getattr(node, "target", None) if isinstance(node, ast.AnnAssign) else None
            if target is not None and getattr(target, "id", "") == symbol:
                assert isinstance(node.value, ast.Dict) and not node.value.keys, \
                    f"{symbol} must ship empty; implementations register themselves"
                break
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", "") == symbol for t in node.targets):
                assert isinstance(node.value, ast.Dict) and not node.value.keys, \
                    f"{symbol} must ship empty; implementations register themselves"
                break
        else:
            pytest.fail(f"{symbol} assignment not found in {rel}")


def test_no_strategy_vocabulary_in_the_public_surface():
    """Names, not prose.

    Deliberately scoped to identifiers rather than a text scan of the whole file. Twice in
    this project a text-based guard fired on its own explanatory docstring, and a guard
    that cries wolf gets deleted. Docstrings may discuss why the COT default was removed;
    what must not exist is a *symbol* named for one strategy's world.
    """
    banned = ("cot", "willco", "comms", "npf", "cmr", "donchian")
    offenders = []
    for path in _modules():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                low = node.name.lower()
                if any(b in low for b in banned):
                    offenders.append(f"{path.relative_to(PKG)}:{node.lineno} {node.name}")
    assert not offenders, "strategy vocabulary in the public API surface:\n  " + "\n  ".join(offenders)
