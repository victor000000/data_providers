#!/usr/bin/env python3
"""Drop bars that cannot be traded: impossible OHLC, or a date that is not real.

Found by the price-file check in 50_checklist.py, which re-reads every file.
These are not rounding artifacts. A rounded bar is off by one unit in the
fourth decimal; these are off by orders of magnitude, one leg at a time:

    MVSTW 22072026   0.4200  0.0010  0.0003  0.0003   open 400x the rest
    MCIC  27041998   17219.70  2410469.75  2410469.75  17219.70
    FBR   17012014   10891.00  132.00  132.00  132.00   open 80x the bar
    HEAT  02022153   -        -       -       -         the year 2153

A bar like that cannot be used and cannot be repaired: widening the range to
contain the odd leg would invent a 140,000% intraday move, and picking one leg
to trust would be guessing. So the row is dropped and recorded, the same way
0.0000 stubs are in 38_trim_zero_rows.py, and the loss is auditable and
reversible from a fresh download.

A third shape looks like a scale error and is NOT dropped. data/raw is Yahoo's
series restated onto today's share basis, so a company that did serial reverse
splits carries enormous historical values - TNXP really did trade around $0.84
on 2012-06-01 and the file reads 10,752,000,000, which is that price on today's
share count. Converting a bar back needs the symbol's splits, and splits.csv
does not have all of them, so the check reports to price_scale_suspects.csv
instead of deleting. See impossible_scale() for why it does not act.

Rows are only dropped beyond a rounding tolerance (1.5 units in the last
stored decimal), so a legitimate bar that rounds by a tick is kept. A bar dated
the build date is also dropped: that session may still be open, and a partial
bar would leave some symbols a day ahead of others.

Log: data/quarantine/inconsistent_rows_removed.csv (cumulative).

  python3 scripts/39_drop_inconsistent_rows.py
"""
import csv
import datetime
import os
import sys

from data_providers.warehouse import config as C

LOG = os.path.join(C.QUARANTINE, "inconsistent_rows_removed.csv")
TOL = 1.5e-4          # one stored tick: the most independent rounding can move
# a date beyond this cannot be a bar for a dataset that runs to today
FUTURE_SLACK = datetime.timedelta(days=1)

# ---- price scale ----------------------------------------------------------
# No US equity except Berkshire Hathaway A has ever traded near seven figures,
# and BRK.A is not in this dataset (only BRK.B), so nothing here can be above
# this. It is deliberately far above the highest real name in the tree - NVR at
# $9,964 - so no legitimate bar is at risk.
MAX_AS_TRADED = 500_000.0
# A bar this many times above the symbol's OWN normal level is a scale break,
# not a price. The median gate keeps the rule off sub-penny names, whose
# history can legitimately move three orders of magnitude (SEEK's median is
# $0.0008; a rule applied there would condemn real bars).
SCALE_BREAK = 1000.0
SCALE_MIN_MEDIAN = 1.0


def scale_context():
    """Splits and spare-source symbols needed to undo the stored price basis."""
    splits = {}
    path = os.path.join(C.CORP_ACTIONS, "splits.csv")
    if os.path.exists(path):
        for r in csv.DictReader(open(path, encoding="utf-8")):
            if not r.get("symbol"):
                continue
            try:
                splits.setdefault(r["symbol"], []).append(
                    (datetime.date.fromisoformat(r["date"]), float(r["ratio"])))
            except (ValueError, TypeError):
                continue
    spare = set()
    path = os.path.join(C.CORP_ACTIONS, "spare_source_fills.csv")
    if os.path.exists(path):
        for r in csv.DictReader(open(path, encoding="utf-8")):
            # "ok-raw-only" holds the raw series in both legs, and the raw leg
            # is as traded either way - only a symbol the spare could NOT fill
            # is still Yahoo's restated series.
            if r.get("symbol") and str(r.get("status", "")).startswith("ok"):
                spare.add(r["symbol"])
    return splits, spare


def as_traded(sym, date, close, splits, spare):
    """The price that actually traded that day, from the stored one.

    The spare source keeps prices as traded, so its bars are already there.
    Everything else is Yahoo restated onto today's share basis, which is undone
    by multiplying back the split ratios that came AFTER the bar.
    """
    if sym in spare or sym not in splits:
        return close
    f = 1.0
    for sd, ratio in splits[sym]:
        if sd > date:
            f *= ratio
    return close * f


SCALE_REPORT = os.path.join(C.QUARANTINE, "price_scale_suspects.csv")


def impossible_scale():
    """{symbol: {DDMMYYYY: as-traded close}} for bars on an impossible scale.

    REPORTED, NEVER DROPPED. This started as a drop rule and that was wrong: it
    condemned 57,491 bars across 53 symbols, every one of them a legitimate
    Yahoo series. The conversion above needs the symbol's COMPLETE split
    history, and data/corporate_actions/splits.csv is a static input that no
    script in this workspace writes - it carries 6,163 splits for 2,447 of the
    8,950 symbols, so ORKA (6 splits at Yahoo, 0 here) looked like a series
    that ramped from $479,001 to $90 for no reason, and it is really the $0.88
    to $90 that traded. A rule that deletes real bars whenever a reference
    table is incomplete is worse than the thing it was written to catch, so the
    finding is written to price_scale_suspects.csv for a human to judge and the
    tree is left alone.
    """
    splits, spare = scale_context()
    out = {}
    if not os.path.isdir(C.RAW):
        return out
    for name in sorted(os.listdir(C.RAW)):
        if not name.endswith(".csv"):
            continue
        sym = name[:-4]
        bars = []
        for line in open(os.path.join(C.RAW, name), encoding="ascii"):
            p = line.strip().split(",")
            if len(p) != 6 or len(p[0]) != 8 or not p[0].isdigit():
                continue
            try:
                c = float(p[4])
                d = datetime.date(int(p[0][4:]), int(p[0][2:4]), int(p[0][:2]))
            except ValueError:
                continue
            a = as_traded(sym, d, c, splits, spare)
            if a != a:                                     # NaN
                continue
            bars.append((p[0], a))
        if not bars:
            continue
        pos = sorted(a for _, a in bars if a > 0)
        med = pos[len(pos) // 2] if pos else 0.0
        bad = {}
        for ds, a in bars:
            if a > MAX_AS_TRADED:
                bad[ds] = a
            elif med >= SCALE_MIN_MEDIAN and a > SCALE_BREAK * med:
                bad[ds] = a
        if bad:
            out[sym] = bad
    return out


def row_fault(line):
    """Why this bar cannot be used, or None. Judged on the bar's own values,
    so the caller can ask the same question of either leg."""
    p = line.split(",")
    if len(p) != 6:
        return "malformed"
    try:
        o, h, l, c = (float(x) for x in p[1:5])
    except ValueError:
        return "non-numeric price"
    why = None
    d = p[0]
    if len(d) == 8 and d.isdigit():
        try:
            ts = datetime.date(int(d[4:8]), int(d[2:4]), int(d[0:2]))
            # Against the REAL today, not the build date. See config.NOW: using the
            # frozen C.TODAY here marked every settled bar after the build as future-dated
            # and rewrote it away.
            if ts > C.NOW + FUTURE_SLACK:
                why = f"date {ts} is in the future"
            elif ts == C.NOW:
                # The build date's session may still be open, and a half-formed
                # bar is not a daily bar: without this, some symbols carry a
                # partial 2026-09-24 bar and the rest end at the settled
                # 2026-09-23. The dataset therefore ends at the last COMPLETED
                # session; 35_refresh_tail.py picks the new bar up once closed.
                why = (f"bar dated the build date ({ts}), whose session may "
                       f"still be open")
        except ValueError:
            why = f"date {d} is not a real date"
    if why is None and any(v != v for v in (o, h, l, c)):
        why = f"non-numeric price ({p[1]},{p[2]},{p[3]},{p[4]})"
    if why is None and min(o, h, l, c) <= 0:
        # zero is a stub (38 handles those) but NEGATIVE is a corrupt stretch:
        # EZEN carried -21.7695 for 110 days
        why = f"non-positive price ({o},{h},{l},{c})"
    if why is None and (h < max(o, l, c) - TOL or l > min(o, h, c) + TOL):
        why = (f"impossible OHLC ({o},{h},{l},{c}): the range does not "
               f"contain the bar")
    return why


def main():
    os.makedirs(C.QUARANTINE, exist_ok=True)
    removed = []
    touched = 0
    # Reported, not acted on - see impossible_scale(). A suspect here means the
    # split history on file does not explain the level, which is evidence about
    # splits.csv as much as about the prices.
    scale = impossible_scale()
    if scale:
        with open(SCALE_REPORT, "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["symbol", "bar", "as_traded_close", "note"])
            for sym in sorted(scale):
                for ds, a in sorted(scale[sym].items()):
                    w.writerow([sym, ds, f"{a:.4f}",
                                "the split history on file does not explain this level"])
        print(f"  NOTE {len(scale)} symbols / "
              f"{sum(len(v) for v in scale.values())} bars do not match their "
              f"recorded split history - reported to {SCALE_REPORT}, nothing dropped")
    syms = set()
    for folder in (C.ADJUSTED, C.RAW):
        if os.path.isdir(folder):
            syms |= {f[:-4] for f in os.listdir(folder) if f.endswith(".csv")}

    for sym in sorted(syms):
        legs = {}
        for folder in (C.ADJUSTED, C.RAW):
            path = os.path.join(folder, sym + ".csv")
            rows = {}
            if os.path.exists(path):
                for line in open(path, encoding="ascii"):
                    s = line.strip()
                    if s:
                        rows[s.split(",")[0]] = s      # keyed by DDMMYYYY
            legs[folder] = (path, rows)
        if not any(rows for _, rows in legs.values()):
            continue

        # A bar is dropped from BOTH legs or from NEITHER. The two files are
        # two readings of the same bar, so a rule that only one of them trips
        # still condemns the bar - and dropping it from one file alone leaves
        # the pair disagreeing about which days exist, which is exactly how
        # EZEN came to hold 110 raw days with no adjusted twin and GSS four.
        # The row is judged twice, once per leg, and the first fault either
        # leg reports is the reason recorded.
        fault = {}
        for folder, (_, rows) in legs.items():
            for d, line in rows.items():
                why = row_fault(line)
                if why and d not in fault:
                    fault[d] = why

        # A bar with no twin is not a pair. The two files are one reading of
        # one bar, so the bar cannot be a row of `prices` with only half of
        # itself - it would carry a NULL adjusted price - and the pair would
        # disagree about which days exist. EZEN held 110 such days, GSS four,
        # DEC 732. Dropping the surviving half costs real bars, which is why it
        # is recorded in full; re-deriving the missing leg from raw x factor
        # would cost more, because those adjusted values never existed and the
        # file would stop being a copy of what the source served.
        every = set()
        for _, rows in legs.values():
            every |= set(rows)
        for d in every:
            if d in fault:
                continue
            held = [f for f, (_, rows) in legs.items() if d in rows]
            if len(held) == 1:
                missing = "adjusted" if held[0] == C.RAW else "raw"
                fault[d] = f"bar has no twin in the {missing} leg"

        # A pair whose legs disagree on the factor is not one bar read twice;
        # the adjusted row is wrong. Judged only where every leg is at or above
        # a cent, so four-decimal rounding cannot manufacture the disagreement -
        # CDMO's two 2018 bars are what is left after that floor, and Sina's
        # adjusted high is 6% off its own adjusted open there.
        a_rows = legs[C.ADJUSTED][1]
        r_rows = legs[C.RAW][1]
        for d in set(a_rows) & set(r_rows):
            if d in fault:
                continue
            try:
                av = [float(x) for x in a_rows[d].split(",")[1:5]]
                rv = [float(x) for x in r_rows[d].split(",")[1:5]]
            except ValueError:
                continue
            if min(av + rv) < 0.01:
                continue
            f = [a / r for a, r in zip(av, rv)]
            if max(f) > min(f) * 1.05:
                fault[d] = (f"the two legs disagree on the adjustment factor "
                            f"({min(f):.4f} to {max(f):.4f})")

        for folder, (path, rows) in legs.items():
            if not rows:
                continue
            kept = [line for d, line in rows.items() if d not in fault]
            dropped = [(line, fault[d]) for d, line in rows.items() if d in fault]
            if not kept and not dropped:
                continue                      # already empty, nothing to do
            if not kept:
                os.remove(path)
                touched += 1
                for row, why in dropped:
                    removed.append({"symbol": sym,
                                    "folder": os.path.basename(folder),
                                    "row": row, "reason": why})
                if not dropped:
                    removed.append({"symbol": sym,
                                    "folder": os.path.basename(folder),
                                    "row": "(file already empty)",
                                    "reason": "empty file removed"})
                print(f"  {sym:9} {os.path.basename(folder):9} "
                      f"-{len(dropped):5} rows, file removed (nothing usable)")
                continue
            if not dropped:
                continue
            touched += 1
            for row, why in dropped:
                removed.append({"symbol": sym,
                                "folder": os.path.basename(folder),
                                "row": row, "reason": why})
            with open(path, "w", encoding="ascii") as fh:
                fh.write("".join(r + "\n" for r in kept))
            print(f"  {sym:9} {os.path.basename(folder):9} -{len(dropped):5} rows,"
                  f" {len(kept):6} left")

    if removed:
        prior = []
        if os.path.exists(LOG):
            seen = {(r["symbol"], r["folder"], r["row"]) for r in removed}
            prior = [r for r in csv.DictReader(open(LOG))
                     if (r["symbol"], r["folder"], r["row"]) not in seen]
        with open(LOG, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["symbol", "folder", "row", "reason"])
            w.writeheader()
            w.writerows(prior + removed)
        print(f"  ({len(prior)} earlier removals kept in the log)")
    nbars = sum(1 for r in removed if not r["row"].startswith("("))
    nfiles = len({(r["symbol"], r["folder"]) for r in removed
                  if r["row"].startswith("(")})
    print(f"\nlog entries {len(removed)}: {nbars} bars dropped, "
          f"{nfiles} empty files removed -> {LOG}")
    print("rebuild the database (60) and re-export (70) so the tree agrees again")


if __name__ == "__main__":
    # `--help` must not be a write. None of these scripts takes an argument, so before
    # this guard `python 39_drop_inconsistent_rows.py --help` ran the whole job: 39 rewrote the raw and
    # adjusted price files, 45 repaired or overwrote them, 60 deleted and rebuilt
    # yahoo.duckdb, 38 and the checkers rewrote their outputs. A flag that reads as
    # "tell me what this does" performing the destructive action is the same failure
    # the main repo hit on 2026-10-05, when a --help sweep rewrote `fills`; the guard
    # there is now shared/helpguard.py, and this is its shape without the import.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__)
        sys.exit(0)
    main()
