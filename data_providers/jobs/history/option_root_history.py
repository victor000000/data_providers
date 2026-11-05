"""Current-issuer historical roots, limited to documented 1:1 symbol conversions.

This lineage is for current-universe history, never for relabelling broker fills.
Adjusted merger contracts (e.g. AVB) require separate deliverable handling.
The manifest covers the downloaders' verified history from February 2024 onward.
"""
import csv
import datetime as dt
from pathlib import Path
from importlib.resources import files
from data_providers.jobs.history.option_universe import occ

from data_providers.runtime import ROOT
PATH = ROOT / "plans/option_root_history.csv"
# The packaged baseline also permits inspection/help before runtime initialization.
# A maintained runtime manifest takes precedence whenever it exists.
LINEAGE = PATH if PATH.exists() else files("data_providers.seed").joinpath("option_root_history.csv")
with LINEAGE.open() as stream:
    HISTORY = {r["underlying"]: (r["previous_root"], r["effective_date"])
               for r in csv.DictReader(stream)}


def windows(underlying, start, end):
    """Inclusive day segments; a conversion is determined by bar day, not expiry."""
    start, end = str(start)[:10], str(end)[:10]
    if start > end:
        return []
    if underlying in HISTORY and start < "2024-02-01":
        raise ValueError("Historical roots verified from 2024-02-01; extend lineage before older requests")
    old, effective = HISTORY.get(underlying, (None, None))
    if old is None:
        return [(None, start, end)]
    out = []
    if start < effective:
        previous = (dt.date.fromisoformat(effective)-dt.timedelta(days=1)).isoformat()
        out.append((old, start, min(end, previous)))
    if end >= effective:
        out.append((None, max(start, effective), end))
    return out


def contract(underlying, root, expiry, cp, strike):
    # A predecessor is literal. Running GOLD through today's map could resolve to B.
    if root is None:
        return occ(underlying, expiry, cp, strike)
    return f"{root}{expiry:%y%m%d}{cp}{int(round(strike*1000)):08d}"


def requests(underlying, expiry, strikes, start, end, held):
    """Keep actual OCC identity in coverage keys across a conversion boundary."""
    out = []
    for root, first, last in windows(underlying, start, end):
        syms, starts = [], []
        for strike in strikes:
            for cp in ("C", "P"):
                symbol = contract(underlying, root, expiry, cp, strike)
                previous = held.get(symbol, "")
                if previous >= last:
                    continue
                syms.append(symbol)
                starts.append(first if previous < first else
                    (dt.date.fromisoformat(previous)+dt.timedelta(days=1)).isoformat())
        if syms:
            out.append((root, syms, min(starts), last))
    return out
