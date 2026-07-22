# API stability policy

If you write a strategy against this framework, you need to know which parts will still be
there next release. This is that promise, and its limits.

It is enforced by `tests/test_public_api.py`, which pins the exact surface below. A policy
nobody checks is a wish, and this one gets broken by a rename in an unrelated refactor
otherwise.

## What is public

**The public API is exactly what appears in a module's `__all__`, in the modules listed in
`tests/test_public_api.py`.** Nothing else. Not a helpfully-named function in a module that
is not listed, not a class you can reach by importing a submodule directly, not an
attribute you found by reading the source.

| Package | Public modules |
|---|---|
| `framework` | `crucible_stack.framework`, `.strategy`, `.config`, `.montecarlo`, `.registry` |
| `engine` | `crucible_stack.engine.simulator`, `.exits` |
| `optimize` | `crucible_stack.optimize`, `.select` |
| `capital` | `crucible_stack.capital` |
| `orchestrate` | `crucible_stack.orchestrate`, `.account_drift` |

A leading underscore means private, but the absence of one does not mean public. The list
is the authority.

## What is deliberately NOT covered

**Registry contents.** `STRATEGY_REGISTRY` and `EXIT_RULES` are public objects whose
*contents* are not. They ship empty and are filled by whoever imports what. No version of
this package can promise what is in them at runtime, because that depends on your imports.

**Numerical output.** Bug fixes to a statistical routine change results, and are not
breaking API changes. If a correction was too weak and gets fixed, your numbers move. That
is the point of the fix. Such changes are called out in the changelog because they matter
more than most signature changes, but they are not governed by this policy. Pin an exact
version if you need bit-identical results.

**Anything in `docs/adr/`.** Decision records describe reasoning at a point in time, not
current behaviour.

**Exception messages and log text.** Match on exception *types*, never on message strings.

## Versioning

Semantic versioning, with the usual pre-1.0 caveat stated plainly rather than buried.

### While `0.x` (now)

**Breaking changes may land in a minor release** (`0.1` to `0.2`). This package is young,
the surface was only pinned recently, and pretending otherwise would mean either lying or
freezing a design before it has been used by anyone but its author.

What is promised even now:

- A breaking change is **never silent**. It appears in `CHANGELOG.md` under `### Changed`
  or `### Removed`, with the migration.
- **Removals go through deprecation where it costs anything to skip it.** The old name
  keeps working for at least one minor release and emits a `DeprecationWarning`. Aliases
  currently kept alive are listed in `DEPRECATED_ALIASES` in `tests/test_public_api.py`.
- **Additions are not breaking**, but they are promises: once a name ships in `__all__`,
  removing it is a breaking change subject to everything above.

Pin `crucible-stack~=0.N.0` if you want to opt out of that.

### At `1.0` and after

- Breaking changes only in a **major** release.
- Deprecations warn for at least **two minor releases** before removal.
- Minor releases add; patch releases fix.

`1.0` is not a schedule. It is the point at which a second author has built something real
against this and the seams have stopped moving.

## Deprecation, concretely

```python
# the old name keeps working
def old_name(*args, **kwargs):
    warnings.warn("old_name is deprecated; use new_name (removed in 0.5)",
                  DeprecationWarning, stacklevel=2)
    return new_name(*args, **kwargs)
```

Every deprecation names the replacement and the release that removes it. "Deprecated" with
no successor and no date is just an unannounced removal.

## Depending on this from a strategy repo

**Import from the listed modules, not from wherever a name happens to live.** Prefer
`from crucible_stack.orchestrate import run_cycle` over reaching into
`crucible_stack.orchestrate.runner`. The former is pinned; the latter is an implementation
detail that may be reorganised.

**Do not import underscore-prefixed names.** If you need one, open an issue and it will
either be made public or given a public equivalent. This has already happened once: a
consumer depended on `montecarlo._max_drawdown`, which is now public as `max_drawdown`,
with the old name kept as an alias. Private-by-convention is not a barrier, and once
something is genuinely depended upon the honest move is to name it public rather than to
pretend it was never used.

**Verify the API, do not trust the version.** A version constraint says what someone
*intended* to require. Under an editable install, the metadata is written once and never
refreshed, so `pip list` can report a version the code has long since left behind. This
package makes that mistake visible for its own dependency in
`tests/test_crucible_compat.py`, which checks the crucible functions it needs by signature
rather than assuming the pin implies them. Copy the pattern if an API matters to you.

## Reporting a break

If an upgrade broke you and it is not in the changelog, that is a bug in this policy, not
just in the code. Open an issue with the import that stopped working.
