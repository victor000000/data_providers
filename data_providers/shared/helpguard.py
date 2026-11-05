import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""helpguard.py — refuse to run a no-argument script when asked for --help.

WHY THIS MODULE EXISTS. Thirty-three scripts here take no arguments and write to a
database, so `script.py --help` used to execute them in full. That is not hypothetical: a
2026-10-05 sweep ran every app with --help and rewrote `fills` (via seed_fills.py, 23
journal fills became 34) and `risk_profile`. Each was guarded with an inline block that
looked up `__doc__` to print a summary before exiting.

THE BUG IN THAT GUARD, which every copy had: `__doc__` is None in all 33 files, so the
summary always printed "(no module docstring; see the source)". A module docstring must be
the FIRST statement in the file; these scripts open with a sys.path bootstrap and
`from data_providers import runtime as bootstrap`, so the string that follows -- the one the reader sees as the
description -- is never bound to `__doc__`. The guard's whole point, telling you what the
script would have done before it declines to do it, was silently absent from every copy.

So the summary is read from the SOURCE instead of from `__doc__`: the first module-level
string literal, wherever it sits. That also keeps the description next to the bootstrap
where the author put it, rather than forcing a mechanical move of 33 docstrings to the top
of their files (which would change `__doc__` for every other consumer too).

    from data_providers.shared import helpguard
    helpguard.handle_help(__file__)          # place after the bootstrap header
"""
import ast


def module_summary(path, max_lines=12):
    """The file's leading string literal, as a list of lines -- not `__doc__`.

    Scans top-level statements for the first bare string expression, so it finds the
    description wherever the bootstrap header pushed it. Returns None if the file has no
    such string, or cannot be parsed.
    """
    try:
        src = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in tree.body:
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            return node.value.value.strip().splitlines()[:max_lines]
    return None


def handle_help(script_file, argv=None, max_lines=12):
    """Print the script's own summary and exit 0 if -h/--help was requested.

    Returns False when this is a normal run, so a caller can also use it as an
    expression. Exits BEFORE the caller touches a database -- placement matters: call it
    after the bootstrap header and before the first connection.
    """
    argv = sys.argv[1:] if argv is None else argv
    if "-h" not in argv and "--help" not in argv:
        return False
    lines = module_summary(script_file, max_lines)
    if lines:
        print("\n".join(lines))
    else:
        print(f"(no module summary in {pathlib.Path(script_file).name}; "
              f"read the source)")
    raise SystemExit(0)
