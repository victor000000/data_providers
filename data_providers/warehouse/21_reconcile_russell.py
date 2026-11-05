#!/usr/bin/env python3
"""Reconcile the three supplied Russell lists against each other.

The Russell family has a hard structural rule: at every date the Russell 3000
is exactly the Russell 1000 plus the Russell 2000, and the two halves never
overlap. The three lists supplied for this workspace do not satisfy it, because
they were maintained independently and each has holes the others do not:

  * a half-index lists a company as LEAVING and then REJOINING months or years
    later, while the Russell 3000 carries it continuously - the "exit" is an
    artifact of that list going stale for the name, not a real index event.
    Every one of these gaps ends on 2026-01-27, the day the half-lists were
    last refreshed, which is the giveaway.
  * the Russell 3000 list is missing windows that one of the halves covers.

Because the invariant is an identity rather than a guess, both defects can be
repaired with proof rather than judgement:

  Rule A - if a half-index has a gap, the Russell 3000 covers every day of that
           gap, and the OTHER half claims none of it, then the company never
           left the first half: it was in the Russell 3000 (given), it was not
           in the other half (given), so it must have been in this one.

  Rule B - if a half-index claims a window the Russell 3000 does not cover,
           the Russell 3000 list is simply missing it: membership of a half
           implies membership of the whole.

Anything the two rules cannot settle - a gap where the other half also claims
part of the window, which would make the halves overlap - is left alone and
reported. Repairs are written to indices/russell_family/reconciliation.csv so
every changed row can be audited or reverted, and the note column on each
repaired row says which rule justified it.

Run 20_build_membership.py after this if the lists change; this script edits
indices/<slug>/membership.csv in place and is idempotent. The canonical order is
20 -> 21 -> 22 from the frozen lists in sources/independent/, which is what
produces these audit files; re-running 21 on an already-repaired tree is a no-op
for membership and rewrites the audit to describe that (empty) run.
"""
import collections
import csv
import datetime
import os
import sys

from data_providers.warehouse import config as C

from data_providers.warehouse import helpguard  # noqa: E402  (a no-argument script must not
helpguard.handle_help(__file__)  #  do its work because someone asked --help)

SLUGS = ("russell1000", "russell2000", "russell3000")
HALVES = ("russell1000", "russell2000")
WHOLE = "russell3000"
FLOOR = datetime.date(1990, 1, 1)
COLS = ["symbol", "company", "start_date", "end_date", "status", "source", "note"]


def load(slug):
    """-> {symbol: [rows]}, rows are the raw dicts, spans kept as dates."""
    per = collections.defaultdict(list)
    path = os.path.join(C.INDICES, slug, "membership.csv")
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if r["symbol"]:
            per[r["symbol"]].append(r)
    return per


def span(row):
    return (datetime.date.fromisoformat(row["start_date"]) if row["start_date"] else None,
            datetime.date.fromisoformat(row["end_date"]) if row["end_date"] else None)


def merge_intervals(iv):
    """Union of possibly overlapping/duplicated spans -> disjoint sorted list."""
    iv = sorted(iv, key=lambda x: (x[0] or FLOOR))
    out = []
    for s, e in iv:
        if out and (out[-1][1] is None or (s is not None and s <= out[-1][1]
                                           + datetime.timedelta(days=1))):
            ps, pe = out[-1]
            if pe is None:
                continue
            if e is None or e > pe:
                out[-1] = (ps, e)
        else:
            out.append((s, e))
    return out


def covers(iv, lo, hi):
    return any((s is None or s <= lo) and (e is None or e >= hi) for s, e in iv)


def touches(iv, lo, hi):
    return any((s is None or s <= hi) and (e is None or e >= lo) for s, e in iv)


def to_row(slug, sym, s, e, source, note):
    return {
        "symbol": sym, "company": "", "start_date": s.isoformat() if s else "",
        "end_date": e.isoformat() if e else "",
        "status": "Removed" if e else "Current",
        "source": source, "note": note,
    }


def main():
    data = {slug: load(slug) for slug in SLUGS}
    spans = {slug: {sym: [span(r) for r in rows]
                    for sym, rows in data[slug].items()} for slug in SLUGS}
    for slug in SLUGS:
        for sym in spans[slug]:
            spans[slug][sym] = merge_intervals(spans[slug][sym])

    audit = []
    symbols = sorted(set().union(*[set(spans[s]) for s in SLUGS]))

    for sym in symbols:
        s1 = spans["russell1000"].get(sym, [])
        s2 = spans["russell2000"].get(sym, [])
        s3 = spans[WHOLE].get(sym, [])
        other = {"russell1000": s2, "russell2000": s1}

        # ---- Rule A: provable gap -> the company never left this half ----
        for slug in HALVES:
            half = spans[slug][sym] if sym in spans[slug] else []
            if not half:
                continue
            for i in range(1, len(half)):
                pe = half[i - 1][1]
                ns = half[i][0]
                if pe is None or ns is None or ns <= pe + datetime.timedelta(days=1):
                    continue
                lo = pe + datetime.timedelta(days=1)
                hi = ns - datetime.timedelta(days=1)
                if lo > C.TODAY or not covers(s3, lo, hi):
                    continue                       # R3000 has a hole too: no proof
                if touches(other[slug], lo, hi):
                    continue                       # would make the halves overlap
                half[i - 1] = (half[i - 1][0], None if half[i][1] is None else half[i][1])
                del half[i]
                audit.append({
                    "symbol": sym, "index_slug": slug, "rule": "A",
                    "change": f"closed spurious gap {lo}..{hi}",
                    "proof": "R3000 covers the whole gap continuously and the "
                             "other half claims none of it, so the company did "
                             "not leave this half"})
                break

        # ---- Rule B: a half claims a window the whole does not cover ----
        for slug in HALVES:
            for s, e in (spans[slug].get(sym) or []):
                lo = s or FLOOR
                hi = e or C.TODAY
                if lo > C.TODAY or covers(s3, lo, hi):
                    continue
                s3.append((lo, hi))
                audit.append({
                    "symbol": sym, "index_slug": WHOLE, "rule": "B",
                    "change": f"extended R3000 to cover {lo}..{hi}",
                    "proof": f"membership of {slug} implies membership of the "
                             f"Russell 3000"})
        spans[WHOLE][sym] = merge_intervals(s3)

    # ---- write the repaired lists back, preserving unrelated provenance ----
    changed = {a["symbol"] for a in audit}
    for slug in SLUGS:
        path = os.path.join(C.INDICES, slug, "membership.csv")
        rows = list(csv.DictReader(open(path, encoding="utf-8")))
        keep = [r for r in rows if r["symbol"] not in changed]
        rebuilt = []
        for sym in symbols:
            if sym in changed and sym in spans[slug]:
                note = next((a["proof"] for a in audit
                             if a["symbol"] == sym and a["index_slug"] == slug), "")
                src = next((r["source"] for r in rows if r["symbol"] == sym),
                           "external-list:user-supplied")
                for s, e in spans[slug][sym]:
                    rebuilt.append(to_row(
                        slug, sym, s, e, src,
                        ("reconciled against the Russell family rule: " + note)
                        if note else ""))
        out = sorted(keep + rebuilt, key=lambda r: (r["start_date"] or "0000",
                                                    r["symbol"]))
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS, restval="")
            w.writeheader()
            w.writerows(out)
        print(f"  {slug:12} {len(rows):6} -> {len(out):6} rows")

    os.makedirs(os.path.join(C.INDICES, "russell_family"), exist_ok=True)
    ap = os.path.join(C.INDICES, "russell_family", "reconciliation.csv")
    with open(ap, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "symbol", "index_slug", "rule", "change", "proof"])
        w.writeheader()
        w.writerows(sorted(audit, key=lambda a: (a["rule"], a["symbol"])))
    print(f"\n{len(audit)} repairs applied -> {ap}")
    print(f"  Rule A (spurious exits closed): {sum(1 for a in audit if a['rule']=='A')}")
    print(f"  Rule B (R3000 coverage gaps closed): "
          f"{sum(1 for a in audit if a['rule']=='B')}")


if __name__ == "__main__":
    main()
