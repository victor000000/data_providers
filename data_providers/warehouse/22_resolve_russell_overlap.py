#!/usr/bin/env python3
"""Make the Russell 1000 and Russell 2000 lists disjoint.

Second defect in the supplied halves. They were stitched together from several
observation snapshots rather than from index events, so a company can be
recorded as being in both halves on the same day. A giveaway is the date
2025-10-13 and 2026-01-27: those are snapshot dates, not Russell reconstitution
dates, and dozens of rows start on them.

Two independent halves cannot both hold a company, so one of the two claims is
spurious. This script resolves the contradiction with the conservative rule
available from the data alone:

    the incumbent wins - where one half's claim sits inside another claim that
    started earlier, the later claim is the duplicate and is pushed back to
    begin the day after the incumbent ends (or dropped, if the incumbent is
    open-ended). The union of the two halves is unchanged either way, so the
    Russell 3000 membership - which both agree on - is not disturbed.

The lists begin at a common floor (1990-07-03, the first date either list
covers), so a great many claims start on that same day and the "started
earlier" test cannot separate them. The same containment idea is applied
there: when the two claims start together and one of them ends strictly later
(or runs to the present), the shorter one is contained in the longer and is
the stale snapshot duplicate. This is the identical failure mode, only with
the evidence at the other end of the window - the source that went stale
recorded an end, the source that stayed current did not.

This is a convention applied to a contradiction in the source, NOT a proof: the
supplied lists do not carry enough information to say which half genuinely held
the company in the overlap. Every resolution is written to
indices/russell_family/overlap_resolutions.csv with both claims, so the choice
can be reviewed or reversed. Rules A and B in 21_reconcile_russell.py, by
contrast, are proofs.

Like 21, this is a step in the chain 20 -> 21 -> 22 and is idempotent: running
it again on an already-disjoint tree changes no membership and rewrites the
audit to describe that (empty) run, so regenerate the audit from the frozen
lists when you need the full trail.
"""
import collections
import csv
import datetime
import os
import sys

from data_providers.warehouse import config as C

from data_providers.warehouse import helpguard  # noqa: E402  (a no-argument script must not
helpguard.handle_help(__file__)  #  do its work because someone asked --help)
import importlib
rec = importlib.import_module("data_providers.warehouse.21_reconcile_russell")

SLUGS = ("russell1000", "russell2000")
COLS = ["symbol", "company", "start_date", "end_date", "status", "source", "note"]
DAY = datetime.timedelta(days=1)


def main():
    data = {slug: rec.load(slug) for slug in SLUGS}
    spans = {slug: {sym: rec.merge_intervals([rec.span(r) for r in rows])
                    for sym, rows in data[slug].items()} for slug in SLUGS}

    audit = []
    for sym in sorted(set(spans["russell1000"]) & set(spans["russell2000"])):
        for slug in SLUGS:
            other = "russell2000" if slug == "russell1000" else "russell1000"
            iv = spans[slug][sym]
            oiv = spans[other][sym]
            out = []
            for s, e in iv:
                lo = s or rec.FLOOR
                clash = None
                for os_, oe in oiv:
                    olo = os_ or rec.FLOOR
                    ohi = oe or C.TODAY
                    if olo > lo or ohi < lo:
                        continue
                    # The other claim starts on the same day (the common
                    # 1990-07-03 floor). It wins only if it also outlasts this
                    # one, which makes this one the contained duplicate.
                    if olo == lo and not (oe is None or
                                          (e is not None and oe > e)):
                        continue
                    clash = (os_, oe)
                    break
                if clash is None:
                    out.append((s, e))
                    continue
                os_, oe = clash
                new_lo = (oe + DAY) if oe else None
                if new_lo is None or (e is not None and new_lo > e):
                    audit.append({
                        "symbol": sym, "index_slug": slug,
                        "dropped_claim": f"{s}..{e or 'present'}",
                        "kept_claim": f"{os_}..{oe or 'present'}",
                        "reason": f"{other} holds it continuously from {os_}; "
                                  f"this claim is a snapshot duplicate"})
                    continue
                audit.append({
                    "symbol": sym, "index_slug": slug,
                    "dropped_claim": f"{s}..{e or 'present'}",
                    "kept_claim": f"{new_lo}..{e or 'present'}",
                    "reason": f"{other} holds it until {oe}; this claim starts "
                              f"inside that window and is a snapshot duplicate"})
                out.append((new_lo, e))
            spans[slug][sym] = rec.merge_intervals(out)

    touched = {a["symbol"] for a in audit}
    for slug in SLUGS:
        path = os.path.join(C.INDICES, slug, "membership.csv")
        rows = list(csv.DictReader(open(path, encoding="utf-8")))
        rebuilt = []
        for sym, iv in spans[slug].items():
            if sym not in touched:
                continue
            src = next((r["source"] for r in rows if r["symbol"] == sym),
                       "external-list:user-supplied")
            why = next((a["reason"] for a in audit
                        if a["symbol"] == sym and a["index_slug"] == slug), "")
            for s, e in iv:
                r = rec.to_row(slug, sym, s, e, src,
                               "overlap resolved: " + why if why else "")
                rebuilt.append(r)
        keep = [r for r in rows if r["symbol"] not in touched]
        out = sorted(keep + rebuilt, key=lambda r: (r["start_date"] or "0000",
                                                    r["symbol"]))
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS, restval="")
            w.writeheader()
            w.writerows(out)
        print(f"  {slug:12} {len(rows):6} -> {len(out):6} rows")

    ap = os.path.join(C.INDICES, "russell_family", "overlap_resolutions.csv")
    with open(ap, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "symbol", "index_slug", "dropped_claim", "kept_claim", "reason"])
        w.writeheader()
        w.writerows(sorted(audit, key=lambda a: (a["symbol"], a["index_slug"])))
    print(f"\n{len(audit)} overlapping claims resolved for "
          f"{len(touched)} symbols -> {ap}")


if __name__ == "__main__":
    main()
