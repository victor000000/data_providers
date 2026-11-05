#!/usr/bin/env python3
import pathlib
import sys

import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

# ── --help guard ─────────────────────────────────────────────────────────────
# No argparse here, so `script.py --help` used to execute this script in full.
# That is not hypothetical: a 2026-10-05 sweep ran every app with --help and
# rewrote `fills`, `risk_profile` and `positions`. The write does not have to be
# in THIS file either -- live_risk_profile.py orchestrates build_positions and
# risk_profile through subprocess, so "does it INSERT?" is the wrong test. The
# rule is simply that a script with no argument parser must not run on --help.
from data_providers.shared import helpguard
helpguard.handle_help(__file__)   # reads the module summary from the SOURCE:
# __doc__ is None here -- the description sits after the bootstrap below, and a
# docstring must be the first statement, so all 33 copies printed "(no docstring)".
"""parse_log.py — re-extract the trading journal (2026.xlsx) into a readable dump.

Usage:
    uv run python parse_log.py [2026.xlsx] [> dump.txt]

Prints every non-empty row as:  R<row> | <col#>:<value> | <col#>:<value> | ...
Use after the user appends new days to 2026.xlsx.
"""
import sys
import pathlib
import openpyxl

# Where the journal actually lives. The old default was ROOT/"journal"/"2026.xlsx",
# a path that has never existed, so the script silently failed for anyone who ran it
# without an argument. `../trading_logs` is the user's hand-maintained source (told to
# me 2026-10-05) and is checked FIRST, because it is updated by hand and the repo copy
# is only as fresh as the last time someone remembered to sync it -- on 2026-10-05 the
# source had been updated that evening while the repo copy was five days stale.
JOURNAL_CANDIDATES = (
    bootstrap.trading_logs_dir() / "2026.xlsx",   # the hand-maintained source
    bootstrap.ROOT / "var" / "raw" / "journal_history" / "2026.xlsx",
)


def newest_journal():
    """(path, why) for the freshest journal we can find, preferring the source dir."""
    for c in JOURNAL_CANDIDATES:
        if c.exists():
            return c, "source dir" if "trading_logs" in str(c) else "repo copy"
    d = bootstrap.ROOT / "var" / "raw" / "journal_history"
    if d.exists():
        xs = sorted(d.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
        if xs:
            return xs[0], "newest in journal_history"
    return None, "not found"


def main():
    if len(sys.argv) > 1:
        path, why = pathlib.Path(sys.argv[1]), "argument"
    else:
        path, why = newest_journal()
        if path is None:
            print("no journal found — checked: "
                  + ", ".join(str(c) for c in JOURNAL_CANDIDATES))
            return
    print(f"# journal: {path} ({why}, mtime {path.stat().st_mtime:.0f})")
    wb = openpyxl.load_workbook(path, data_only=True)
    for ws in wb.worksheets:
        print(f"### SHEET: {ws.title} | {ws.max_row} rows x {ws.max_column} cols")
        for r, row in enumerate(ws.iter_rows(values_only=True), 1):
            cells = [(i, v) for i, v in enumerate(row, 1) if v is not None and str(v).strip() != ""]
            if not cells:
                continue
            parts = []
            for i, v in cells:
                s = str(v).replace("\n", " ")
                if len(s) > 120:
                    s = s[:120] + "…"
                parts.append(f"{i}:{s}")
            print(f"R{r} | " + " | ".join(parts))


# The guard is not cosmetic. Without it this file RUNS on import — it would re-extract the
# journal and print it into whatever process imported it. Thirteen scripts in this repo were
# like that, several of them writing to a database, which made "import it to test it" a
# destructive act. See the restructure plan.
if __name__ == "__main__":
    main()
