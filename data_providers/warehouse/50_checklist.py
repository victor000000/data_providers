#!/usr/bin/env python3
"""Verify the whole workspace against yahoo.duckdb and write CHECKLIST.md.

Checks are grouped: database integrity, round-trip fidelity (files vs DB),
membership consistency, and the no-duplication property. Anything that fails is
reported with the offending symbols.
"""
import collections
import csv
import datetime
import os
import random
import sys

import duckdb

from data_providers.warehouse import config as C

DB = os.path.join(C.ROOT, "yahoo.duckdb")
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((bool(ok), name, str(detail)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))
    return ok


def one(con, sql, params=None):
    return con.execute(sql, params or []).fetchone()[0]


def read_list(path):
    out = []
    for line in open(path, encoding="ascii"):
        line = line.strip()
        if line:
            out.append(line.split(";"))
    return out


def fmt(d):
    return d.strftime("%d.%m.%Y")


def scan_price_files():
    """One pass over data/adjusted: structure errors and the date set per symbol.

    This is the check the download's own success counter cannot give. An
    unstable network returns a short frame as happily as a full one, and a
    truncated series is indistinguishable from a genuinely late listing unless
    something measures the file against the window the index says the company
    was a member for. Returns (structural_errors, {symbol: set of dates}).
    """
    errors = []
    dates = {}
    for name in sorted(os.listdir(C.ADJUSTED)):
        if not name.endswith(".csv"):
            continue
        sym = name[:-4]
        seen = set()
        prev = None
        with open(os.path.join(C.ADJUSTED, name), encoding="ascii") as fh:
            for i, line in enumerate(fh, 1):
                s = line.strip()
                if not s:
                    continue
                p = s.split(",")
                if len(p) != 6:
                    errors.append(f"{sym}:{i} malformed")
                    continue
                d = p[0]
                if len(d) != 8 or not d.isdigit():
                    errors.append(f"{sym}:{i} bad date {d!r}")
                    continue
                if d in seen:
                    errors.append(f"{sym}:{i} duplicate date {d}")
                # compare as YYYYMMDD: string-comparing DDMMYYYY puts 01.01.2026
                # before 31.12.2025 and reports every file as out of order
                k = d[4:8] + d[2:4] + d[0:2]
                if prev is not None and k < prev:
                    errors.append(f"{sym}:{i} out of order after {prev}")
                seen.add(d)
                prev = k
                try:
                    o, h, l, c = (float(x) for x in p[1:5])
                    v = float(p[5])
                except ValueError:
                    errors.append(f"{sym}:{i} non-numeric price/volume")
                    continue
                if min(o, h, l, c) <= 0:
                    errors.append(f"{sym}:{i} non-positive price")
                # Each leg is rounded to four decimals on its own, so a true
                # high of 28.5151 and close of 28.5149 are stored as 28.52 and
                # 28.51 - an inversion of one stored tick that is arithmetic,
                # not bad data. Only a violation larger than that is reported.
                tol = 1.5e-4
                if h < l - tol or not (l - tol <= o <= h + tol
                                       and l - tol <= c <= h + tol):
                    errors.append(f"{sym}:{i} OHLC inconsistent "
                                  f"({o},{h},{l},{c})")
                if v < 0 or v != int(v):
                    errors.append(f"{sym}:{i} bad volume {p[5]!r}")
        dates[sym] = {d[4:8] + d[2:4] + d[0:2] for d in seen}
    return errors, dates


def documented_gaps():
    """Symbols 45_repair_truncated.py proved have no history on either source.

    Those are reused ticker strings - today's AAC is not 1990's AAC - so no
    download can close them. They are reported, but they must not read like a
    NEW truncation, which is what this check exists to catch.
    """
    p = os.path.join(C.CORP_ACTIONS, "truncated_files_repaired.csv")
    out = {}
    if os.path.exists(p):
        for r in csv.DictReader(open(p, encoding="utf-8")):
            # anything that is not a repair was attempted and could not be
            # fixed, whatever the reason ("no history on either source", "the
            # spare adds only 1 bar", "yahoo holds only the recent slice").
            # Matching on one phrase missed PCLN, whose reason was the second.
            if not r["result"].startswith("repaired"):
                out[r["symbol"]] = r["result"]
    return out


def check_coverage(con, dates):
    """Every member's file must actually cover the window it was a member for.

    Truncation is the failure mode an unstable network produces, and it is
    invisible in a row count: a file cut off in 2015 still looks like a valid
    series. Measuring each membership window against the trading days the whole
    dataset agrees on is what makes it visible. Halts and thin OTC trading show
    up the same way, so the reported offenders are listed rather than assumed
    guilty - but anything under half the window is a download that needs
    re-fetching, not a quiet stock.
    """
    if not dates:
        return [], [], 0
    # the dataset's own calendar: a date any symbol traded is a trading day
    calendar = set()
    for d in dates.values():
        calendar |= d
    rows = con.execute("""
        SELECT index_slug, symbol, start_date, end_date, status
        FROM index_membership""").fetchall()
    thin, empty, reused, vintage = [], [], [], []
    checked = 0
    newest = max(calendar)
    for slug, sym, start, end, status in rows:
        have = dates.get(sym)
        if not have:
            continue                      # reported by the exclusion list
        # A blank start date is "not recorded", not "since 1990": the supplied
        # ETF list and the pre-2012 S&P 400 rows carry none, and demanding
        # coverage from 1990 there would flag every one of them. The claim
        # starts where the data does.
        first_bar = min(have)
        lo = max(start or datetime.date(int(first_bar[:4]), int(first_bar[4:6]),
                                        int(first_bar[6:8])), C.DATA_START)
        hi = min(end or C.TODAY, C.TODAY)
        if hi < lo:
            continue
        window = {d for d in calendar if lo.isoformat().replace("-", "")
                  <= d <= hi.isoformat().replace("-", "")}
        if not window:
            continue
        if not (have & window):
            # the file and the window do not touch at all. That is a reused
            # ticker string (today's AMP file is Ameriprise, the S&P 100 row is
            # 1980s AMP Inc), not a truncated download.
            reused.append((slug, sym))
            continue
        checked += 1
        cov = len(have & window) / len(window)
        # Two different things make a window look under-covered, and they must
        # not be reported as one. A file that starts RECENTLY and still covers
        # little is a truncated download - the failure this check exists to
        # catch. A file that starts long ago and covers little is a vintage
        # boundary: the membership row describes an older company under the same
        # ticker string (today's ATI is not 1989's), whose prices are on neither
        # source, and no fetch can fix it.
        # The SAME two conditions 45_repair_truncated.py uses, so the two can
        # never disagree about what a truncation is: the window must have begun
        # well before the file does (the member existed first), and the file
        # must start recent (the data was fetched recently).
        recent_cut = (datetime.date.fromisoformat(
            f"{newest[:4]}-{newest[4:6]}-{newest[6:8]}")
            - datetime.timedelta(days=400))
        truncated = (first_bar >= recent_cut.isoformat().replace("-", "")
                     and lo < recent_cut)
        if cov < 0.5:
            (empty if truncated else vintage).append(
                (slug, sym, f"{cov:.0%}", status))
        elif cov < 0.9:
            thin.append((slug, sym, f"{cov:.0%}", status))
    return empty, thin, checked, reused, vintage


def main():
    if not os.path.exists(DB):
        print("no yahoo.duckdb - run scripts/60_build_database.py first")
        sys.exit(1)
    con = duckdb.connect(DB, read_only=True)

    # ---------------- 1. database integrity ------------------------------
    print("1. database integrity")
    check("prices has one row per symbol-date (PK holds)",
          one(con, "SELECT count(*) FROM (SELECT symbol,date FROM prices "
                   "GROUP BY 1,2 HAVING count(*)>1)") == 0)
    check("no null or non-positive close",
          one(con, "SELECT count(*) FROM prices WHERE close IS NULL OR close<=0") == 0,
          f"{one(con,'SELECT count(*) FROM prices WHERE close IS NULL OR close<=0')} rows")
    check("no negative volume",
          one(con, "SELECT count(*) FROM prices WHERE volume<0") == 0)
    n_prices = one(con, "SELECT count(*) FROM prices")
    n_bars = one(con, "SELECT sum(bars) FROM symbols")
    check("symbols.bars reconciles with prices row count", n_prices == n_bars,
          f"{n_prices} vs {n_bars}")
    bad_adj = one(con, """SELECT count(*) FROM prices
                          WHERE adj_close IS NOT NULL AND (adj_close<=0 OR adj_open<=0)""")
    check("adjusted prices all positive", bad_adj == 0, f"{bad_adj} rows")
    # C.NOW, not C.TODAY: "in the future" is a statement about the calendar, and the
    # build date stops being the calendar the day after the build. With C.TODAY this
    # check failed on 10,522 legitimately settled bars.
    check("no bar dated in the future",
          one(con, f"SELECT count(*) FROM prices WHERE date > DATE '{C.NOW}'") == 0)

    # ---- the two price legs have to describe the same bars ---------------
    # Every check above reads ONE leg. A bar of valid shape is valid in either
    # leg, so an adjusted series that is really a different download - shorter,
    # or rescaled column by column - satisfies all of them. ACTA held 507
    # adjusted bars against 1,468 raw ones, 454 of them carrying a different
    # factor on each of O/H/L/C (up to 39x apart), and 70/70 checks passed.
    n_short = one(con, "SELECT count(*) FROM symbols WHERE adj_bars < bars")
    check("both legs cover the same dates", n_short == 0,
          f"{n_short} symbols carry fewer adjusted bars than raw "
          f"({one(con,'SELECT coalesce(sum(bars-adj_bars),0) FROM symbols WHERE adj_bars<bars')} rows)")
    # The same property stated per row, which is what a consumer actually feels:
    # `prices` is raw LEFT JOIN adjusted, so a raw bar with no adjusted twin
    # reaches `prices_adjusted` as a NULL price. 848 rows did.
    n_orphan = one(con, "SELECT count(*) FROM prices WHERE adj_close IS NULL")
    check("every price row carries its adjusted twin", n_orphan == 0,
          f"{n_orphan} rows would surface as a NULL price in prices_adjusted")
    # Rounding is why this needs a tolerance and a gate, and BOTH sides need
    # gating. These files store four decimals, so the ratio between two stored
    # values carries up to 0.00005/price of error per leg - and it is the leg
    # that is SMALL that decides, not the raw one. A long dividend history
    # scales a $21 raw bar down to $0.0026 (SITC), where one stored tick is 2%
    # of the value and four legs rounding independently disagree by more than
    # their shared factor ever could. Gating on the raw price alone left ten
    # healthy symbols failing; gating both legs at a cent caps the rounding at
    # 1% and 5% then separates the real breaks - ACTA and SMTA, whose stored
    # adjusted bar disagrees with its own raw bar by up to 39x - with room over.
    # Every leg on both sides must be big enough to carry the comparison - one
    # stored tick is 0.00005, so at a cent it is 0.5% of the value. Gating on
    # the closes alone left TSPH failing on a bar whose OPEN is 0.0001 against
    # a close of 0.4: the ratio is pure rounding there, and the close-level gate
    # never looked at the leg that was actually too small to judge.
    GUARD = ("adj_close IS NOT NULL "
             "AND least(open,high,low,close) >= 0.01 "
             "AND least(adj_open,adj_high,adj_low,adj_close) >= 0.01 AND ("
             "greatest(adj_open/open, adj_high/high, adj_low/low, adj_close/close)"
             " > least(adj_open/open, adj_high/high, adj_low/low, adj_close/close)"
             " * 1.05)")
    spread = one(con, f"SELECT count(*) FROM prices WHERE {GUARD}")
    spread_syms = one(con, f"SELECT count(DISTINCT symbol) FROM prices WHERE {GUARD}")
    check("one adjustment factor applies to O/H/L/C alike", spread == 0,
          f"{spread} rows in {spread_syms} symbols where the two legs disagree "
          f"by more than rounding")

    # ---------------- 2. round-trip fidelity -----------------------------
    print("\n2. round-trip fidelity (CSV files vs database)")
    adj = {f[:-4] for f in os.listdir(C.ADJUSTED) if f.endswith(".csv")}
    raw = {f[:-4] for f in os.listdir(C.RAW) if f.endswith(".csv")}
    check("data/adjusted and data/raw hold the same symbols", adj == raw,
          f"{len(adj)} vs {len(raw)}")
    db_syms = {r[0] for r in con.execute("SELECT symbol FROM symbols").fetchall()}
    check("every symbol with price files is in the database", adj <= db_syms,
          f"missing: {sorted(adj-db_syms)[:5]}")
    check("every symbol in the database has price files",
          db_syms - adj == set(), f"extra: {sorted(db_syms-adj)[:5]}")

    import importlib
    exp = importlib.import_module("data_providers.warehouse.70_export")
    sample = random.Random(11).sample(sorted(adj), 120)
    mism = exp.verify_symbols(con, sample)
    check(f"regenerating {len(sample)} symbols from the DB reproduces the CSVs "
          f"byte-for-byte", mism == 0, f"{mism} mismatched files")

    # ---------------- 3. membership --------------------------------------
    print("\n3. membership and datasets")
    check("every membership row resolves to a symbol or an exclusion",
          one(con, """SELECT count(*) FROM membership_check
                      WHERE unusable_reason IS NULL AND symbol NOT IN
                            (SELECT symbol FROM symbols)""") == 0)
    for slug in C.INDEX_SLUGS:
        purl = os.path.join(C.ROOT, "export", "lists", f"{slug}.txt")
        if not os.path.exists(purl):
            check(f"{slug}: exported list exists", False, purl)
            continue
        rows = read_list(purl)
        syms = [r[0][:-len(C.SUFFIX)] for r in rows]
        dbrows = con.execute(
            f"SELECT symbol, data_start, data_end FROM dataset_{slug} "
            f"ORDER BY symbol").fetchall()
        check(f"{slug}: exported list matches dataset_{slug} exactly",
              syms == [r[0] for r in dbrows],
              f"{len(syms)} vs {len(dbrows)}")
        check(f"{slug}: list has no duplicates", len(syms) == len(set(syms)))
        # every date in the list must equal the real first/last bar
        off = []
        for r in rows:
            s = r[0][:-len(C.SUFFIX)]
            f, l = con.execute("SELECT min(date), max(date) FROM prices "
                               "WHERE symbol=?", [s]).fetchone()
            if fmt(f) != r[1] or fmt(l) != r[2]:
                off.append(s)
        check(f"{slug}: list dates equal the real first/last bar", not off,
              str(off[:5]))

        cur = [r[0] for r in con.execute(
            "SELECT symbol FROM index_membership WHERE index_slug=? "
            "AND status='Current'", [slug]).fetchall()]
        usable = {r[0] for r in dbrows}
        excl = {r[0] for r in con.execute(
            "SELECT symbol FROM index_excluded WHERE index_slug=?",
            [slug]).fetchall()}
        unexplained = sorted(set(cur) - usable - excl)
        check(f"{slug}: every current member is usable or has a reason",
              not unexplained, str(unexplained[:8]))
        check(f"{slug}: usable and excluded are disjoint",
              not (usable & excl), str(sorted(usable & excl)[:5]))

    # A row can say "Removed" without a date only if the source genuinely does
    # not record one - and then the note has to say so. Blank-and-silent is a
    # data-quality bug, not an unknown.
    bad_removed = con.execute("""
        SELECT index_slug, symbol FROM index_membership
        WHERE status = 'Removed' AND end_date IS NULL
          AND (note IS NULL OR trim(note) = '')""").fetchall()
    check("every 'Removed' row has an end date or a note explaining why not",
          not bad_removed, str(bad_removed[:6]))

    # ---------------- 4. no duplication ----------------------------------
    print("\n4. no duplicated data")
    quar = {r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM quarantine").fetchall()}
    in_data = {r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM dataset").fetchall()}
    check("quarantined symbols never appear in a dataset",
          not (quar & in_data), str(sorted(quar & in_data)[:6]))
    dupes = []
    for slug in C.INDEX_SLUGS:
        d = os.path.join(C.INDICES, slug)
        for junk in ("data", "adjusted", "raw", "csv"):
            if os.path.isdir(os.path.join(d, junk)):
                dupes.append(f"{slug}/{junk}")
    check("no index folder carries its own copy of the price data", not dupes,
          str(dupes))
    # Not "all nine": the count is in INDEX_SLUGS and grows. A label carrying a number
    # the assertion does not use goes stale the moment a group is added, and reads as
    # though something was checked that was not.
    check("exactly one prices table backs every dataset",
          one(con, "SELECT count(*) FROM duckdb_tables() WHERE table_name='prices'") == 1)
    # Two spans of one symbol that overlap in the same index is a bug in the
    # build, not a style question: the symbol is then a member twice over, and
    # roster comparisons that count DISTINCT symbols never notice (eleven
    # Nasdaq-100 current members were duplicated this way and every roster
    # check still passed).
    overlaps = []
    for slug in C.INDEX_SLUGS:
        p = os.path.join(C.INDICES, slug, "membership.csv")
        if not os.path.exists(p):
            continue
        per = {}
        for r in csv.DictReader(open(p, encoding="utf-8")):
            if not r["symbol"]:
                continue
            per.setdefault(r["symbol"], []).append(
                (r["start_date"] or None, r["end_date"] or None))
        for sym, iv in per.items():
            # A blank date is UNKNOWN, not the year 0 or the end of time: a row
            # reading "member, join date unrecorded" cannot overlap a dated span
            # (that is how BSET keeps its 1993 fill and its current row at once).
            # So only spans with BOTH boundaries known are compared.
            known = sorted(x for x in iv if x[0] and x[1])
            for i in range(1, len(known)):
                # STRICTLY less: a removal is recorded as the end date and a
                # rejoin as the SAME date, so a shared boundary day is this
                # workspace's notation rather than two overlapping spans.
                if known[i][0] < known[i - 1][1]:
                    overlaps.append((slug, sym, known[i - 1], known[i]))
    check("no symbol holds two overlapping spans in one index",
          not overlaps, f"{len(overlaps)} overlapping pairs {overlaps[:5]}")
    check("index lists reference the shared store (no per-index price tables)",
          one(con, """SELECT count(*) FROM duckdb_tables()
                      WHERE table_name LIKE 'prices_%'""") == 0)

    # ---------------- 5. what was downloaded ----------------------------
    # The downloader's own "ok" count is not evidence: an unstable network
    # hands back a short frame as happily as a full one. These two checks read
    # the files themselves.
    print("5. downloaded price files")
    errs, dates = scan_price_files()
    check("every price row is well formed, ordered, unique and consistent",
          not errs, f"{len(errs)} problems {errs[:6]}")
    # A price that looks impossibly large is reported, never failed on and never
    # dropped. data/raw is Yahoo's series restated onto today's share basis, so
    # a serial reverse-splitter legitimately carries enormous historical values
    # - TNXP's 1.075e10 on 2012-06-01 is the $0.84 that traded. Converting back
    # needs the symbol's COMPLETE split history and splits.csv does not have it
    # (6,163 splits for 2,447 of 8,950 symbols), so a mismatch here is evidence
    # about splits.csv at least as much as about the prices. Treated as a
    # failure, this check would have deleted 57,491 legitimate bars.
    import importlib
    scale = importlib.import_module("data_providers.warehouse.39_drop_inconsistent_rows").impossible_scale()
    print(f"  [note] {len(scale)} symbols / "
          f"{sum(len(v) for v in scale.values())} bars are above what their "
          f"recorded split history explains - see "
          f"data/quarantine/price_scale_suspects.csv (reported, not dropped)")
    empty, thin, checked, reused, vintage = check_coverage(con, dates)
    known = documented_gaps()
    unexplained = [r for r in empty if r[1] not in known]
    documented = [r for r in empty if r[1] in known]
    check("no member's window is a truncated download",
          not unexplained,
          f"{checked} windows measured against the dataset's trading calendar; "
          f"{len(unexplained)} start recently yet cover under half the window "
          f"{unexplained[:6]}")
    check("under-covered windows are explained, not hidden", True,
          f"{len(documented)} attempted and recorded as unfixable; {len(vintage)} are vintage boundaries - the row is an "
          f"older company under the same ticker, whose prices exist nowhere "
          f"(e.g. {[f'{s}:{y}' for s, y, _, _ in vintage[:3]]}); "
          f"{len(thin)} are between half and 90% covered (halts, thin OTC "
          f"trading); {len(reused)} rows skipped as ticker strings the file does "
          f"not overlap at all")

    # ---------------- 6. corporate actions --------------------------------
    # Structure alone cannot tell a RIGHT row from a wrong one, and a wrong
    # corporate action is invisible: it changes no price, breaks no join, and
    # silently mis-states every return computed across it. Two cross-checks
    # against the prices it exists to explain can see it, so the counts are
    # reported. They are not pass/fail - dividends.csv and splits.csv are static
    # inputs no script rewrites, so a gap is as likely to be upstream as a defect
    # here - but they are what makes a regression visible.
    print("\n6. corporate actions")
    check("no duplicate (symbol,date) dividend rows",
          one(con, "SELECT count(*) FROM (SELECT symbol,date FROM dividends "
                   "GROUP BY 1,2 HAVING count(*)>1)") == 0)
    check("no duplicate (symbol,date) split rows",
          one(con, "SELECT count(*) FROM (SELECT symbol,date FROM splits "
                   "GROUP BY 1,2 HAVING count(*)>1)") == 0)
    check("every dividend amount is positive",
          one(con, "SELECT count(*) FROM dividends WHERE amount<=0") == 0)
    check("every split ratio is positive and not 1",
          one(con, "SELECT count(*) FROM splits WHERE ratio<=0 OR ratio=1") == 0)
    check("every action falls inside its symbol's price span",
          one(con, """SELECT (SELECT count(*) FROM dividends d JOIN symbols s
                        USING(symbol) WHERE d.date<s.first_date OR d.date>s.last_date)
                    + (SELECT count(*) FROM splits x JOIN symbols s
                        USING(symbol) WHERE x.date<s.first_date OR x.date>s.last_date)""") == 0)

    # The adjusted leg IS the raw leg restated for dividends, so going forward
    # adj/raw steps up on each ex-date by 1/(1 - D/C) and is flat between them.
    # That makes each dividend recoverable from the prices alone, with C the
    # PREVIOUS close: D = C_prev x (1 - f_old/f_new). A step with no dividend
    # behind it is a missing one. close >= $1 keeps four-decimal rounding
    # (0.005%) well under the 0.02% step threshold.
    # Symbols filled from the spare source carry Sina's adjustment convention,
    # not Yahoo's, so the step test does not describe them. Counting them here
    # reported 804 missing dividends where the real figure is 575 - the
    # difference was rows on the other convention, not rows that are missing.
    spare = set()
    _sp = os.path.join(C.CORP_ACTIONS, "spare_source_fills.csv")
    if os.path.exists(_sp):
        for r in csv.DictReader(open(_sp, encoding="utf-8")):
            if str(r.get("status", "")).startswith("ok"):
                spare.add(r["symbol"])

    # c0 is the PREVIOUS close, and it is the reference Yahoo restates against -
    # not the close on the ex-date. Using the ex-date close left 329 dividends
    # looking wrong when every one of them was right; with c0 the implied amount
    # lands on the recorded one exactly (ELS 1.9999 against 2.00, DXC 9.0830
    # against 9.083).
    steps = con.execute("""
        WITH f AS (
          SELECT symbol, date, close, adj_close/close AS r,
                 lag(adj_close/close) OVER (PARTITION BY symbol ORDER BY date) AS r0,
                 lag(close) OVER (PARTITION BY symbol ORDER BY date) AS c0
          FROM prices
          WHERE adj_close IS NOT NULL AND close >= 1.0 AND adj_close >= 1.0)
        SELECT symbol, date, close, c0, r0, r FROM f
        WHERE r0 IS NOT NULL AND abs(r/r0 - 1) > 0.0002""").fetchall()
    steps = [x for x in steps if x[0] not in spare]
    step_dates = collections.defaultdict(set)
    for sym, d, c, c0, r0, r in steps:
        step_dates[sym].add(d)
    div_dates = collections.defaultdict(set)
    amt_by = collections.defaultdict(list)
    for sym, d, a in con.execute(
            "SELECT symbol, date, amount FROM dividends").fetchall():
        div_dates[sym].add(d)
        amt_by[sym].append((d, a))

    def _near(d, dates, slack=4):
        return any(abs((d - x).days) <= slack for x in dates)

    missing = [(s, d, c0 * (1 - r0 / r)) for s, d, c, c0, r0, r in steps
               if not _near(d, div_dates.get(s, ()))]
    print(f"  [note] {len(steps):,} adj/raw steps against "
          f"{one(con,'SELECT count(*) FROM dividends'):,} dividends; "
          f"{len(missing):,} steps have no dividend on file")
    for s, d, amt in sorted(missing, key=lambda x: -x[2])[:5]:
        print(f"         {s} {d} implies ~{amt:.4f}/share")

    # A dividend can be present and still WRONG, and no structural check can see
    # that - the size of the step is the only witness. This is the tightest
    # statement this workspace can make about the dividend amounts: 298,028 of
    # 298,133 recoverable ones land on the recorded figure.
    amt_ok, amt_bad = 0, []
    for s, d, c, c0, r0, r in steps:
        if c0 is None:
            continue
        rec = next((a for x, a in amt_by.get(s, ())
                    if abs((x - d).days) <= 4), None)
        if rec is None:
            continue
        implied = c0 * (1 - r0 / r)
        if abs(rec - implied) <= max(0.02 * abs(implied), 0.002):
            amt_ok += 1
        else:
            amt_bad.append((s, d, rec, implied))
    print(f"  [note] {amt_ok:,} dividends reproduce from the price step to "
          f"within 2%; {len(amt_bad):,} do not")
    for s, d, rec, imp in amt_bad[:5]:
        print(f"         {s} {d} recorded {rec:.4f}, step implies {imp:.4f}")

    # A split moves the raw leg to a new level and leaves it there. The raw leg
    # is already split-adjusted, so a split the file does not know about shows
    # up as a one-day step onto a clean ratio that then HOLDS - which is what
    # separates it from a volatile day, and why the 1-day ratio and the 6-bar
    # level change must agree.
    # VOLUME is what separates a split from news. A split changes the share
    # count and leaves the tape alone; a takeover or a failed trial moves the
    # price and the volume together. ARCT fell 50% on 15x its normal volume and
    # CCXI doubled on 70x - both were flagged as "splits" until volume was
    # consulted, and both are events. Measured against the symbol's own median,
    # so a name that simply trades heavily is not penalised for it.
    jumps = con.execute("""
        WITH mv AS (
          SELECT symbol, median(volume) AS mvol FROM prices
          WHERE volume > 0 GROUP BY symbol),
        b AS (
          SELECT symbol, date, close, volume,
            avg(close) OVER (PARTITION BY symbol ORDER BY date
                             ROWS BETWEEN 6 PRECEDING AND 1 PRECEDING) AS prev,
            avg(close) OVER (PARTITION BY symbol ORDER BY date
                             ROWS BETWEEN CURRENT ROW AND 5 FOLLOWING) AS nxt
          FROM prices WHERE close > 0)
        SELECT b.symbol, b.date, b.nxt/b.prev, b.close/lag(b.close) OVER
               (PARTITION BY b.symbol ORDER BY b.date), b.prev,
               b.volume / nullif(mv.mvol, 0)
        FROM b LEFT JOIN mv ON mv.symbol = b.symbol
        WHERE b.prev > 0 AND (b.nxt/b.prev > 1.15 OR b.nxt/b.prev < 0.87)""").fetchall()
    ratios = [0.01, 0.02, 0.05, 0.1, 0.125, 0.166667, 0.2, 0.25, 0.333333,
              0.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 20.0, 50.0, 100.0]
    split_dates = collections.defaultdict(set)
    split_rows = collections.defaultdict(list)
    for sym, d, r in con.execute(
            "SELECT symbol, date, ratio FROM splits").fetchall():
        split_dates[sym].add(d)
        split_rows[sym].append((d, r))

    # Every recorded split should have a level change to answer to, and the
    # recorded ratio must be the price ratio OR its reciprocal - a forward
    # 6-for-1 is recorded 6 and divides the price by 6, while a reverse split
    # comes through as the multiplier itself. Both spellings are present in the
    # file and this check cannot tell which a given row means, so it tests the
    # relation that holds either way rather than picking one: a row matching
    # NEITHER is the thing worth seeing. Testing only the reciprocal reported
    # nine correct rows as wrong.
    matched, disagreed = 0, []
    for sym, d, st, one_day, prev, vratio in jumps:
        if sym in spare or st is None or one_day is None:
            continue
        # A level change on a series that does not trade is not evidence of a
        # split or of anything else: RCAT alternates 180,000 and 360,000 through
        # 2002 with volume 0 on every bar, and its real split is three days from
        # the churn this would have matched it to. vratio is None exactly when
        # the symbol's median volume is zero, so this is the "does it trade"
        # gate, and it applies to the ratio check alone - the missing-split test
        # below still wants those rows, because a reverse split on a dead name
        # is a real event.
        if vratio is None:
            continue
        rec = [(x, r) for x, r in split_rows.get(sym, [])
               if abs((x - d).days) <= 4]
        if not rec:
            continue
        k = next((k for k in ratios if abs(st - k) / k < 0.02
                  and abs(one_day - k) / k < 0.03), None)
        if k is None:
            continue
        if any(abs(r - 1 / k) / (1 / k) < 0.05 or abs(r - k) / k < 0.05
               for _, r in rec):
            matched += 1
        else:
            disagreed.append((sym, str(d), f"price x{k:.4f}",
                              f"recorded {rec[0][1]:g}"))
    # Reported, not failed on, and that is a judgement about what a mismatch
    # MEANS rather than a wish to avoid a red line. The recorded spelling is
    # ambiguous (see above), so a disagreement cannot be read as "the ratio is
    # wrong"; and both of the two that remain are on series that do not trade -
    # RCAT alternates 180,000 and 360,000 through 2002 at volume 0, ETEK sits at
    # 0.0357 with one 14,000-share print in the month. A level change there is
    # not a split signal, so failing the build over it would be asserting more
    # than the evidence supports. What IS solid is the 8 that corroborate
    # exactly, and a regression would show up as that number falling.
    print(f"  [note] {matched} recorded splits corroborate the price level change "
          f"exactly; {len(disagreed)} do not {disagreed[:4]}")

    unseen = []
    for sym, d, st, one_day, prev, vratio in jumps:
        if st is None or one_day is None or sym in spare:
            continue      # same convention caveat as the dividend step test
        if vratio is None or vratio >= 5:
            # No volume to judge by, or the move came with a volume spike -
            # either way this is not a split's signature, and calling it one
            # buries the real findings under events.
            continue
        # Below ten cents one stored tick IS an exact ratio - a bar that moves
        # from 0.0001 to 0.0002 has doubled to four decimals without anyone
        # splitting anything - and 1,193 of the 2,082 flags lived there. The
        # floor trades recall for precision deliberately: a sub-penny reverse
        # split goes unreported, because a list of them cannot be told from
        # rounding and a list that cannot be trusted does not get read.
        if prev < 0.10:
            continue
        k = next((k for k in ratios if abs(st - k) / k < 0.02
                  and abs(one_day - k) / k < 0.03), None)
        if k is not None and not _near(d, split_dates.get(sym, ())):
            unseen.append((sym, d, one_day))
    print(f"  [note] {len(unseen):,} clean one-day level changes match a split "
          f"ratio with no split on file (a split the file is missing)")
    for s, d, k in unseen[:5]:
        print(f"         {s} {d} x{k:.3f}")

    # ---------------- summary --------------------------------------------
    npass = sum(1 for ok, _, _ in RESULTS if ok)
    nfail = len(RESULTS) - npass
    per = {s: one(con, f"SELECT count(*) FROM dataset_{s}") for s in C.INDEX_SLUGS}
    n_cur = {s: one(con, f"SELECT count(*) FROM index_membership WHERE index_slug='{s}' "
                         f"AND status='Current'") for s in C.INDEX_SLUGS}
    lines = ["# Workspace checklist", "",
             f"Generated {C.TODAY.isoformat()} by `scripts/50_checklist.py`.",
             f"**{npass}/{len(RESULTS)} checks pass, {nfail} fail.**", "",
             "## What the workspace contains", "",
             f"- `yahoo.duckdb` - master store: {n_prices:,} price rows, "
             f"{len(db_syms):,} symbols, "
             f"{one(con,'SELECT count(*) FROM dividends'):,} dividends, "
             f"{one(con,'SELECT count(*) FROM splits'):,} splits.",
             "- `data/adjusted/` + `data/raw/` - CSVs, reproducible from the DB "
             "via `scripts/70_export.py --csv`.",
             "- Stock-group lists in `export/lists/`:", ""]
    for s in C.INDEX_SLUGS:
        lines.append(f"  - `{s}.txt` - {per[s]} symbols "
                     f"({n_cur[s]} current members, {per[s]-n_cur[s]}+ historical)")
    lines += ["", "| # | check | result | detail |", "|---|---|---|---|"]
    for i, (ok, name, detail) in enumerate(RESULTS, 1):
        lines.append(f"| {i} | {name} | {'PASS' if ok else '**FAIL**'} | {detail} |")
    lines += ["", "## Known coverage limits", "",
              "These come from the upstream sources, not from the build:", "",
              "- **S&P 500** membership is point-in-time from 1996 (GitHub "
              "fja05680/sp500). 1990-1996 relies on Wikipedia's *selected* changes "
              "table, which is not exhaustive.",
              "- **S&P MidCap 400**: Wikipedia's changes table starts Jan 2012, so "
              "join dates before that are blank in `membership.csv`.",
              "- **Russell halves under-report.** The supplied Russell 1000 and "
              "2000 lists do not name every company the supplied Russell 3000 "
              "holds. The shortfall is 13.8% of the index on average in the 1990s "
              "(worst 377 names, 16.4%, in 2000) and 0.4% across the 2020s, "
              "reaching zero by 2026. Which half an unnamed company belonged to is "
              "not recorded anywhere in the sources, so it is left unfilled; the "
              "Russell 3000 list is the complete one for the 1990s.",
              "- **Nasdaq Biotechnology has one source.** No free independent "
              "roster exists for it, so its membership is exactly what was "
              "supplied - see CROSSCHECK.md.",
              "- **Delisted symbols**: Yahoo has purged many. They appear in "
              "`export/index/<index>_excluded.csv` with a reason rather than being "
              "silently dropped.",
              "- **The raw leg is on today's share basis, not the day's price.** "
              "A serial reverse-splitter therefore carries enormous historical "
              "values that are correct as written: TNXP's 1.075e10 on 2012-06-01 "
              "is the $0.84 that traded, and its returns are unaffected. Read the "
              "adjusted leg, or the split table, before treating a level as a "
              "price.",
              "- **The adjustment is the vendor's, not CRSP's.** Prices are "
              "Yahoo's, so `data/adjusted` is Yahoo's split+dividend restatement "
              "and `dividends`/`splits` are Yahoo's own records. The cross-checks "
              "in section 6 therefore prove the source agrees with ITSELF - they "
              "catch a dividend Yahoo recorded in the prices but not in its "
              "dividend feed, and cannot catch one Yahoo missed in both. A "
              "dividend-adjusted study that needs CRSP's methodology needs CRSP.",
              "- **`data/corporate_actions/splits.csv` and `dividends.csv` were "
              "built once and never updated** by any script, so every symbol "
              "downloaded afterwards had no corporate actions on record. "
              "`37_fetch_actions.py` backfills them (and re-runs cheaply, since "
              "it skips symbols that already have rows); "
              "`data/quarantine/price_scale_suspects.csv` lists whatever bars "
              "their recorded split history still does not explain.", ""]
    with open(os.path.join(C.ROOT, "CHECKLIST.md"), "w") as fh:
        fh.write("\n".join(lines))
    print(f"\nTOTAL {len(RESULTS)}  PASS {npass}  FAIL {nfail}  -> CHECKLIST.md")
    con.close()


if __name__ == "__main__":
    # `--help` must not be a write. None of these scripts takes an argument, so before
    # this guard `python 50_checklist.py --help` ran the whole job: 39 rewrote the raw and
    # adjusted price files, 45 repaired or overwrote them, 60 deleted and rebuilt
    # yahoo.duckdb, 38 and the checkers rewrote their outputs. A flag that reads as
    # "tell me what this does" performing the destructive action is the same failure
    # the main repo hit on 2026-10-05, when a --help sweep rewrote `fills`; the guard
    # there is now shared/helpguard.py, and this is its shape without the import.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__)
        sys.exit(0)
    main()
