"""schwab_clock_batch.py — the export clock for a DIRECTORY of statements.

`schwab_clock` measures one statement's clock against the log. That is not enough on
its own: the first load of a new trading day has no anchor, because the fills it would
match against are exactly the ones not yet written. So a statement that cannot be
anchored borrows the offset of a sibling that could, provided the two print the same
zone token -- and anything still unresolved returns None so the caller refuses it
rather than writing timestamps it cannot vouch for.
"""
import datetime as dt
import re

from data_providers.jobs.trades.schwab_clock import ZONES, offset_from_log


def zone_token(rows, limit=60):
    """The zone abbreviation this statement prints, e.g. 'CST', or None.

    Only ever PROPAGATES an offset to a sibling statement, never looks one up: 'CST' is
    China Standard Time here and Central Standard Time elsewhere. See `resolve`.
    """
    # Takes either a flat row list or the {section: rows} dict `sections()` returns;
    # the caller has the latter, and scanning only its keys finds no zone.
    if isinstance(rows, dict):
        rows = [r for sec in rows.values() for r in sec]
    for r in rows[:limit]:
        for cell in (r if isinstance(r, (list, tuple)) else [r]):
            for tok in ZONES:
                if re.search(rf"\b{tok}\b", str(cell)):
                    return tok
    return None


def resolve(con, path_rows, fills_by_path):
    """Offsets for a batch of statements: anchor each, then propagate by zone token.

    A statement whose fills all predate the log -- the first load of a new trading day
    -- cannot anchor itself, because the fills it would match against are the ones not
    yet written. It borrows the offset of a sibling that could, provided the two print
    the same zone token. Anything still unresolved stays None.
    """
    out, measured = {}, {}
    for path, rows in path_rows:
        off, ev = offset_from_log(con, fills_by_path[path]) if con is not None else (None, None)
        out[path] = (off, ev, "measured from a fill the log already had")
        if off is not None and zone_token(rows):
            measured[zone_token(rows)] = off
    for path, rows in path_rows:
        tok = zone_token(rows)
        if out[path][0] is None and tok in measured:
            out[path] = (measured[tok], None, f"{tok} as measured on a sibling statement")
    return out


def pick(resolved, header):
    """(offset, how) for one statement, from the batch's resolution plus its header.

    A clock PRINTED in the report beats one INFERRED from a shared fill -- it is the
    statement talking about itself. This flavour prints none, so in practice the anchor
    wins, but a fallback that silently outranks the evidence is how the two drift.
    """
    offset, evidence, how = resolved
    if header != dt.timedelta(0):
        return header, "from the report header"
    if offset is not None and evidence and evidence[1] is not None:
        how = f"measured from {evidence[0]} {evidence[1]:g}{evidence[2]} {evidence[3]}"
    return offset, how
