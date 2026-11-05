"""schwab_statement.py — shared shape of a Schwab Account Statement.

The statement is a sequence of named sections, and both the trade parsers and the
account parsers need the same two primitives: cut it into sections, and key a leg the
way every other table does.
"""
import datetime as dt
import re

from data_providers.jobs.trades import load_schwab_csv as sw


def sections(rows):
    """Split the statement into {heading: [rows]}.

    The file is a sequence of named sections, each an unindented title followed by its
    own header line and body. A blank/ragged line ends nothing -- sections run until the
    next title -- so the title test is the only boundary and it must be exact, or a
    description cell reading "Options" becomes a section break.
    """
    TITLES = {"Cash Balance", "Futures Statements", "Forex Statements",
              "Account Order History", "Account Trade History", "Options",
              "Profits and Losses", "Account Summary"}
    out, cur = {}, None
    for r in rows:
        head = (r[0].strip() if r else "")
        if head in TITLES:
            cur = head
            out[cur] = []
            continue
        if cur is not None:
            out[cur].append(r)
    return out


def occ(underlying, exp, strike, cp):
    """The OCC key every other table joins on.

    The statement's own `Option Code` is NOT it -- it prints `TLT261120P74`, with the
    strike un-padded, while `positions`, `fills` and the IBKR loaders all use the
    8-digit form. Keying on the file's spelling would make every Schwab leg a phantom
    mismatch, which is the bug the sibling loader already fixed once for symbols.
    """
    return f"{underlying}{exp:%y%m%d}{cp}{int(round(float(strike) * 1000)):08d}"


def _when(s):
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2}) (\d{1,2}):(\d{2}):(\d{2})", (s or "").strip())
    if not m:
        return None
    mo, d, y, hh, mm, ss = (int(x) for x in m.groups())
    return dt.datetime(2000 + y, mo, d, hh, mm, ss)
