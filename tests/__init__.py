"""Marks `tests` as a real package rather than a namespace package.

Not cosmetic. Some third-party distributions install a top-level `tests` package
directly into site-packages (vectorbt 0.28.2 is one), and a regular package there
shadows an implicit namespace package of the same name. Without this file,
`--book-factory tests.test_runner:build` resolves to *someone else's* tests
directory and fails with a ModuleNotFoundError that points nowhere near the cause.
"""
