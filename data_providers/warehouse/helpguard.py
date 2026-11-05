#!/usr/bin/env python3
"""Refuse to run a no-argument script when asked for --help.

WHY THIS EXISTS, AND WHY IT IS A COPY. Measured 2026-10-06: `30_download_missing.py
--help` DOWNLOADED SIX SYMBOLS. None of the twenty numbered scripts here takes an
argument, so `--help` is not a question -- it is a full run. The parent repo has
`shared/helpguard.py` for exactly this, after a 2026-10-05 sweep ran every app with
`--help` and rewrote `fills` and `risk_profile`.

This is a copy rather than an import on purpose. README.md states the subproject's
design property in one line -- "scripts/config.py is self-locating, so the folder can
be moved or renamed without editing anything" -- and reaching up to `../shared/` for a
forty-line stdlib helper would break it. The two files are meant to drift only if the
subproject ever leaves.

THE SUMMARY IS READ FROM THE SOURCE, not from `__doc__`. A module docstring has to be
the FIRST statement, and these scripts open with a sys.path bootstrap, so the string a
reader sees as the description is not bound to `__doc__` -- every one of the parent
repo's 33 guarded scripts printed "(no module docstring)" before this was fixed. The
first module-level string literal is found by AST instead, wherever it sits.

    from data_providers.warehouse import helpguard
    helpguard.handle_help(__file__)          # after the imports, before main()
"""
import ast
import pathlib
import sys


def module_summary(path, max_lines=12):
    """The file's leading string literal, as a list of lines -- not `__doc__`."""
    try:
        tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            return node.value.value.strip().splitlines()[:max_lines]
    return None


def handle_help(script_file, argv=None, max_lines=12):
    """Print this script's own summary and exit 0 if -h/--help was asked for.

    Returns False on a normal run. Exits BEFORE the caller opens a database or a
    socket -- which is the whole point: placement matters more than the logic.
    """
    argv = sys.argv[1:] if argv is None else argv
    if "-h" not in argv and "--help" not in argv:
        return False
    lines = module_summary(script_file, max_lines)
    print("\n".join(lines) if lines else
          f"(no module summary in {pathlib.Path(script_file).name}; read the source)")
    raise SystemExit(0)


if __name__ == "__main__":
    print(__doc__)
