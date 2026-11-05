#!/usr/bin/env python3
"""Merge the externally supplied constituent lists into the membership files.

Why this exists: the build sources for S&P 500 (GitHub) and S&P MidCap 400
(Wikipedia's changes table) do not reach back to 1990 - the S&P 400 table starts
in 2012, and the S&P 500 point-in-time file starts 1996. The supplied lists
carry the missing 1990-1996 membership, so this fills that window.

Scope is deliberately narrow: only spans that (a) overlap 1990-1996 and (b) are
not already represented in the database are added. Everything else keeps the
original build provenance, so the two sources stay distinguishable via the
`source` column.

Added rows are marked `external-list:user-supplied` and every one is recorded in
indices/<slug>/external_additions.csv so the import is auditable.
"""
import csv
import datetime
import os
import re
import sys

from data_providers.warehouse import config as C

from data_providers.warehouse import helpguard  # noqa: E402  (a no-argument script must not
helpguard.handle_help(__file__)  #  do its work because someone asked --help)

LO = datetime.date(1990, 1, 1)
HI = datetime.date(1996, 12, 31)
TOL = datetime.timedelta(days=10)

LISTS = {
    "sp500": "sp500_user_list.txt",
    "nasdaq100": "ndx_user_list.txt",
    "spmidcap400": "sp400_user_list.txt",
}


def pdate(s):
    s = (s or "").strip()
    if not s:
        return None
    d, m, y = s.split(".")
    return datetime.date(int(y), int(m), int(d))


def parse(path):
    """-> [(ticker, start, end)] with the .D and -YYYY decorations stripped."""
    out = []
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        p = line.split(";")
        base = re.sub(r"\.D$", "", p[0]).strip()
        tick = re.sub(r"-\d{4}$", "", base)
        # The lists spell share classes with a dot (BF.B, MOG.A); this workspace
        # uses the dash form Yahoo uses (BF-B, MOG-A). Normalising here stops a
        # dotted spelling being imported as a second company alongside the same
        # company already stored under its dashed ticker.
        tick = tick.replace(".", "-")
        # a vendor vintage spelling of a company that kept trading under
        # another symbol must fold onto the symbol Yahoo serves (config.py)
        tick = C.VINTAGE_RENAMES.get(tick, tick)
        out.append((tick, pdate(p[1]) if len(p) > 1 else None,
                    pdate(p[2]) if len(p) > 2 else None))
    return out


def already_covered(spans, t, s, e):
    """Does the DB already hold this ticker over (roughly) this window?"""
    for a, b in spans.get(t, []):
        # same open-ended membership
        if e is None and b is None:
            return True
        if e is not None and b is not None and abs((b - e).days) <= 10:
            return True
        # our span already straddles the requested window
        if a is not None and s is not None and a <= s and (b is None or b >= e or e is None):
            return True
    return False


def main():
    total = 0
    for slug, fn in LISTS.items():
        path = os.path.join(C.SOURCES, "independent", fn)
        if not os.path.exists(path):
            print(f"  {slug:12} no external list present - skipped")
            continue

        mpath = os.path.join(C.INDICES, slug, "membership.csv")
        rows = list(csv.DictReader(open(mpath)))
        # EVERY row counts, including one with both dates blank. Those are the
        # open-ended memberships whose join date the build never recorded (the
        # Nasdaq-100 roster reads that way), and skipping them made
        # already_covered() blind to them: it then re-added eleven of them as
        # duplicates - AAPL, MSFT, CSCO and friends each ended up with two
        # "Current" rows. The roster checks could not see it, because they
        # count distinct symbols.
        spans = {}
        for r in rows:
            spans.setdefault(r["symbol"], []).append((
                datetime.date.fromisoformat(r["start_date"]) if r["start_date"] else None,
                datetime.date.fromisoformat(r["end_date"]) if r["end_date"] else None))

        added = []
        for tick, s, e in parse(path):
            if not tick:
                continue
            # only the 1990-1996 window, and only spans that touch it
            if (s is not None and s > HI) or (e is not None and e < LO):
                continue
            if already_covered(spans, tick, s, e):
                continue
            # Clamp the far end twice: to the window being filled, and to the
            # day before whatever the build already holds. Without the second
            # clamp a fill could overlap an existing span - the S&P 500 build
            # starts 1996-01-02 while this window ends 1996-12-31, so 63 symbols
            # (T, C, MRSH, AGN...) ended up as members twice over for that year.
            fill_e = e
            if fill_e is None or fill_e > HI:
                fill_e = HI
            for a, b in spans.get(tick, []):
                if a is not None and s is not None and a > s and a <= fill_e:
                    fill_e = a - datetime.timedelta(days=1)
            if s is not None and fill_e < s:
                continue                  # the build already covers this window
            spans.setdefault(tick, []).append((s, fill_e))
            added.append({
                "symbol": tick, "company": "",
                "start_date": s.isoformat() if s else "",
                "end_date": fill_e.isoformat() if fill_e else "",
                "status": "Removed" if e else "Current",
                "source": "external-list:user-supplied",
                "note": (f"filled from the supplied list; "
                         f"true span {s or '?'}..{e or 'present'}"),
            })

        if not added:
            print(f"  {slug:12} nothing to add")
            continue

        out = os.path.join(C.INDICES, slug, "external_additions.csv")
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=[
                "symbol", "company", "start_date", "end_date", "status",
                "source", "note"])
            w.writeheader()
            w.writerows(sorted(added, key=lambda r: (r["start_date"], r["symbol"])))

        rows.extend(added)
        with open(mpath, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=[
                "symbol", "company", "start_date", "end_date", "status",
                "source", "note"], restval="")
            w.writeheader()
            w.writerows(rows)
        total += len(added)
        print(f"  {slug:12} +{len(added):4} spans from the supplied list "
              f"-> {out}")

    print(f"\nmerged {total} pre-1996 membership spans")


if __name__ == "__main__":
    main()
