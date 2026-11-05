#!/usr/bin/env python3
"""Build one normalized membership history per index.

Every index produces the same file with the same columns, so downstream code
never needs to know which source an index came from:

    indices/<slug>/membership.csv
        symbol      Yahoo-style ticker
        company     company name
        start_date  date the symbol joined the index (YYYY-MM-DD, '' if unknown)
        end_date    date it left (YYYY-MM-DD, '' = still a member)
        status      Current | Removed
        source      which upstream dataset the row came from

Sources (see sources/wikipedia/PROVENANCE.md):
    sp500       fja05680/sp500 point-in-time GitHub data (1996+), topped up
                from the Wikipedia changes table for the 1990-1996 window
    spmidcap400 Wikipedia current members + changes table (2012+)
    nasdaq100   Nasdaq100_all_constituents.xlsx (previously validated)
    dowjones    Wikipedia 30-component snapshots, diffed into change events
"""
import csv
import datetime
import importlib
import os
import re
import sys

import io

import pandas as pd
from bs4 import BeautifulSoup

from data_providers.warehouse import config as C

from data_providers.warehouse import helpguard  # noqa: E402  (a no-argument script must not
helpguard.handle_help(__file__)  #  do its work because someone asked --help)
from data_providers.warehouse import djia_names

COLS = ["symbol", "company", "start_date", "end_date", "status", "source", "note"]

# Blank dates mean "unknown", which is different from "not applicable". The
# note column carries what the source actually said, so information like
# "before 27.01.1995" or "removal date not recorded" survives into the export
# instead of being flattened into an empty field.

MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def yahoo_ticker(t):
    """Wikipedia/GitHub ticker -> Yahoo ticker (BRK.B -> BRK-B)."""
    return str(t).strip().upper().replace(".", "-")


def parse_date(s):
    """Parse the several date spellings the sources use -> date or None."""
    if s is None:
        return None
    s = str(s).strip()
    if not s or s.lower() in ("nan", "present", "current"):
        return None
    if s.lower().startswith("before"):
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def iso(d):
    return d.isoformat() if d else ""


def soup_of(path):
    return BeautifulSoup(open(path, encoding="utf-8").read(), "lxml")


def read_html_tables(path):
    return pd.read_html(io.StringIO(open(path, encoding="utf-8").read()))


def clean_name(v):
    v = " ".join(str(v).split())
    v = re.sub(r"\[\d+\]", "", v)                  # [12] citation markers
    v = re.sub(r"[↑↓†‡*]+", "", v).strip()
    v = re.sub(r"\(formerly[^)]*\)", "", v).strip()
    return v


def write_membership(slug, rows):
    """rows: list of dicts with COLS keys."""
    d = os.path.join(C.INDICES, slug)
    os.makedirs(d, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r["start_date"] or "0000", r["symbol"]))
    with open(os.path.join(d, "membership.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, restval="")
        w.writeheader()
        w.writerows(rows)
    cur = [r for r in rows if r["status"] == "Current"]
    print(f"  {slug:12} {len(rows):5} ever   {len(cur):5} current   "
          f"{len(rows)-len(cur):5} removed")
    return len(rows)


def merge_spans(spans):
    """Union of possibly overlapping/duplicated (start, end) spans.

    The supplied lists split a company's membership wherever their own data has
    a gap, and two vendor ticker vintages that fold onto one symbol (below)
    arrive as two overlapping windows. Left alone, that produces two membership
    rows for one company over the same years - which is not wrong point-in-time
    but does break the round-trip and set comparisons the checks rely on.
    `end=None` means still a member.
    """
    out = []
    for s, e in sorted(spans, key=lambda x: (x[0] or datetime.date.min)):
        if out and (out[-1][1] is None
                    or (s is not None and s <= out[-1][1] + datetime.timedelta(days=1))):
            ps, pe = out[-1]
            if pe is None:
                continue
            if e is None or e > pe:
                out[-1] = (ps, e)
        else:
            out.append((s, e))
    return out


# The vintage ticker table lives in config.py so the builder and the
# external-list merge below apply exactly the same folding.


# --------------------------------------------------------------------------
# S&P 100  (built directly from the supplied list - see build_sp100)
# --------------------------------------------------------------------------

# The supplied S&P 100 list was cut before the September 2026 quarterly
# rebalance and carries the June Honeywell swap with vendor dates that overlap
# by 17 days, which a single index cannot do. Both events are documented by the
# index provider itself, so they are corrected here rather than left to look
# like a disagreement:
#
#   2026-06-23 release - Honeywell Aerospace (HONA), spun off from Honeywell
#     International (HON), joined the S&P 500 on 2026-06-29 and replaced HON in
#     the S&P 100 effective before the open on 2026-06-30.
#   2026-09-04 release - quarterly rebalance effective before the open on
#     2026-09-21: DELL, PANW, ANET and SNDK joined the S&P 100; HONA, NKE, SPG
#     and CL left it.
#
# Each entry is (op, symbol, start, end, note): "close" ends a still-open span,
# "replace" rewrites both ends of one, and "add" appends a span the list never
# carried (end=None means still a member). Every corrected row keeps the
# provider's date in its note so the edit is auditable, and the supplied list
# itself in sources/independent/ stays untouched as the record of what arrived.
SP100_RECENT_FIXES = [
    ("close", "HON", "2026-06-29", None,
     "replaced in the S&P 100 by the spun-off Honeywell Aerospace, effective "
     "2026-06-30 (S&P DJI release 2026-06-23)"),
    ("replace", "HONA", "2026-06-30", "2026-09-21",
     "joined the S&P 100 effective 2026-06-30 and left it in the 2026-09-21 "
     "rebalance (S&P DJI releases 2026-06-23 and 2026-09-04)"),
    ("close", "NKE", "2026-09-21", None,
     "left the S&P 100 in the 2026-09-21 quarterly rebalance (S&P DJI release "
     "2026-09-04)"),
    ("close", "SPG", "2026-09-21", None,
     "left the S&P 100 in the 2026-09-21 quarterly rebalance (S&P DJI release "
     "2026-09-04)"),
    ("close", "CL", "2026-09-21", None,
     "left the S&P 100 in the 2026-09-21 quarterly rebalance (S&P DJI release "
     "2026-09-04)"),
    ("add", "DELL", "2026-09-21", None,
     "joined the S&P 100 in the 2026-09-21 quarterly rebalance (S&P DJI release "
     "2026-09-04)"),
    ("add", "PANW", "2026-09-21", None,
     "joined the S&P 100 in the 2026-09-21 quarterly rebalance (S&P DJI release "
     "2026-09-04)"),
    ("add", "ANET", "2026-09-21", None,
     "joined the S&P 100 in the 2026-09-21 quarterly rebalance (S&P DJI release "
     "2026-09-04)"),
    ("add", "SNDK", "2026-09-21", None,
     "joined the S&P 100 in the 2026-09-21 quarterly rebalance (S&P DJI release "
     "2026-09-04)"),
]


def fix_sp100_recent(rows):
    """Apply the provider-documented S&P 100 events the supplied list misses."""
    by_sym = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)
    for fix in SP100_RECENT_FIXES:
        op, sym = fix[0], fix[1]
        start, end, why = fix[2], fix[3], fix[4]
        if op == "add":
            rows.append({
                "symbol": sym, "company": "",
                "start_date": start, "end_date": "",
                "status": "Current", "source": "external-list:user-supplied",
                "note": why,
            })
            print(f"  [fix]    sp100 {sym} added {start} - {why[:60]}...")
            continue
        spans = by_sym.get(sym, [])
        if not spans:
            print(f"  [warn]   sp100 {sym}: no span to {op} - skipped")
            continue
        # the last span by start date, whether or not the vendor had already
        # closed it: HON arrives closed at 2026-07-25 and still needs the
        # provider's 2026-06-29 end
        r = max(spans, key=lambda x: x["start_date"] or "")
        r["note"] = ((r["note"] + "; ") if r["note"] else "") + why
        if op == "close":
            r["end_date"], r["status"] = start, "Removed"
            print(f"  [fix]    sp100 {sym} closed {start}")
        else:
            r["start_date"], r["end_date"], r["status"] = start, end, "Removed"
            print(f"  [fix]    sp100 {sym} re-dated {start}..{end}")
    return rows


def build_from_list(slug, filename):
    """Membership built directly from an externally supplied constituent list.

    The supplied lists are the source for the indexes that have no public
    history reaching back far enough to build from: the S&P 100, the Nasdaq
    Biotechnology Index and the three Russell lists - and for the custom ETF
    group, which is not an index at all but is stored the same way. For those
    there is also no second source left to cross-check the names against, which
    is recorded in CROSSCHECK.md rather than papered over. Rows are marked
    `external-list:user-supplied`.

    The ETF list carries no dates, so those members have no recorded join date;
    their real span is the price history each file holds, which is what the
    dataset view and the exported lists report.

    A ticker may hold several spans (the lists split membership where their own
    data has gaps), so every span becomes its own membership row.
    """
    path = os.path.join(C.SOURCES, "independent", filename)
    if not os.path.exists(path):
        print(f"  [warn] {slug}: list {filename} absent - index skipped")
        return []
    rows = []
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        p = line.split(";")
        base = re.sub(r"\.D$", "", p[0]).strip()
        # -YYYY disambiguates a reused ticker; dots are the lists' share-class
        # spelling (BRK.B) where this workspace uses Yahoo's dash form (BRK-B)
        t = re.sub(r"-\d{4}$", "", base).replace(".", "-")
        t = C.VINTAGE_RENAMES.get(t, t)
        if not t:
            continue
        s = parse_date(p[1]) if len(p) > 1 else None
        e = parse_date(p[2]) if len(p) > 2 else None
        rows.append({"symbol": t, "start": s, "end": e})

    # group per symbol and union the spans, so a company that left and rejoined
    # (or arrived under two vendor vintages) gets disjoint, non-duplicated rows
    per = {}
    for r in rows:
        per.setdefault(r["symbol"], []).append((r["start"], r["end"]))
    out = []
    for t in sorted(per):
        for s, e in merge_spans(per[t]):
            out.append({
                "symbol": t, "company": "",
                "start_date": s.isoformat() if s else "",
                "end_date": e.isoformat() if e else "",
                "status": "Removed" if e else "Current",
                "source": "external-list:user-supplied",
                "note": "",
            })
    return out


# --------------------------------------------------------------------------
# S&P 500
# --------------------------------------------------------------------------
def build_sp500():
    rows = []
    path = os.path.join(C.SOURCES, "github_sp500", "sp500_ticker_start_end.csv")
    for r in csv.DictReader(open(path)):
        t = yahoo_ticker(r["ticker"])
        if not t:
            continue
        end = (r.get("end_date") or "").strip()
        rows.append({
            "symbol": t, "company": "",
            "start_date": (r.get("start_date") or "").strip(),
            "end_date": end,
            "status": "Removed" if end else "Current",
            "source": "github:fja05680/sp500",
        })
    known = {r["symbol"] for r in rows}

    # The GitHub file starts 1996-01-02. Add Wikipedia change events that fall
    # before that, so the requested 1990-1996 window is not silently empty.
    html = os.path.join(C.WIKI_RAW, "sp500_changes.html")
    df = read_html_tables(html)[1]
    df.columns = ["date", "add_t", "add_s", "rem_t", "rem_s", "reason"]
    for _, r in df.iterrows():
        d = parse_date(r["date"])
        if not d or d >= datetime.date(1996, 1, 2):
            continue
        for col, is_add in ((("add_t", "add_s"), True),
                            (("rem_t", "rem_s"), False)):
            tk, nm = r[col[0]], r[col[1]]
            t = yahoo_ticker(tk) if pd.notna(tk) else ""
            if not t or t == "NAN" or t in known:
                continue
            known.add(t)
            # 'Added' means the symbol joined on d; 'Removed' means it left on
            # d. Recording a removal as a start date would claim the symbol was
            # a member from that day on - the opposite of what happened.
            rows.append({
                "symbol": t, "company": clean_name(nm) if pd.notna(nm) else "",
                "start_date": d.isoformat() if is_add else "",
                "end_date": "" if is_add else d.isoformat(),
                "status": "Removed",
                "source": "wikipedia:sp500 changes (pre-1996)",
                # the other end of the span predates the point-in-time record
                "note": ("left before the 1996 point-in-time record" if is_add
                         else "joined before the 1996 point-in-time record"),
            })

    # current membership + company names from the live article
    live = read_html_tables(os.path.join(C.WIKI_RAW, "sp500_live.html"))[0]
    names = {yahoo_ticker(r["Symbol"]): clean_name(r["Security"])
             for _, r in live.iterrows()}
    for r in rows:
        if r["symbol"] in names:
            r["company"] = names[r["symbol"]]

    # Reconcile against the LIVE roster. The historical sources lag by one
    # rebalance - both GitHub and dfjmax still listed BLDR/TAP/TTD as members
    # after the 2026-09-21 rebalance replaced them with BE/ILMN/P - so the live
    # article has the final say on who is a member today.
    added = {yahoo_ticker(r["Symbol"]): parse_date(r["Date added"])
             for _, r in live.iterrows()}
    known_current = {r["symbol"] for r in rows
                     if r["status"] == "Current" and not r["end_date"]}
    latest = max((d for d in added.values() if d), default=None)

    for t, d in added.items():
        if t in known_current:
            continue
        rows.append({
            "symbol": t, "company": names.get(t, ""),
            "start_date": d.isoformat() if d else "", "end_date": "",
            "status": "Current", "source": "wikipedia:sp500 live roster",
            "note": "re-added; date from the live article's Date added",
        })
    for t in sorted(known_current - set(added)):
        for r in rows:
            if r["symbol"] == t and r["status"] == "Current" and not r["end_date"]:
                r["end_date"] = latest.isoformat() if latest else ""
                r["status"] = "Removed"
                r["note"] = ("removal date inferred from the latest published "
                             "rebalance date")

    # Correct join dates that are really just our data horizon. The GitHub
    # source begins 1996-01-02, so a company that joined in 1982 is stored as
    # joining in 1996 - which is wrong and misleads any point-in-time query.
    # Wikipedia's "Date added" holds the true join date for current members, so
    # adopt it wherever it is earlier than what we hold.
    fixed = 0
    for t, d in added.items():
        if not d:
            continue
        for r in rows:
            if r["symbol"] != t or r["status"] != "Current" or r["end_date"]:
                continue
            if not r["start_date"] or r["start_date"] > d.isoformat():
                r["start_date"] = d.isoformat()
                r["note"] = ((r.get("note", "") + "; ") if r.get("note") else "") + (
                    "join date from Wikipedia's Date added; the GitHub source "
                    "floors at 1996-01-02")
                fixed += 1
    if fixed:
        print(f"  [fix]    {fixed} S&P 500 join dates corrected from the 1996 "
              f"data horizon to the real date")
    return rows


# --------------------------------------------------------------------------
# S&P MidCap 400
# --------------------------------------------------------------------------
def build_spmidcap400():
    tables = read_html_tables(os.path.join(C.WIKI_RAW, "spmidcap400_live.html"))
    cur = tables[0]
    chg = tables[1]
    chg.columns = ["date", "add_t", "add_s", "rem_t", "rem_s", "reason"]

    events = []
    for _, r in chg.iterrows():
        d = parse_date(r["date"])
        if not d:
            continue
        events.append((d, r))
    events.sort(key=lambda x: x[0])

    # walk the change log to open/close a window per ticker
    open_at, done = {}, {}
    for d, r in events:
        for tk, nm in ((r["rem_t"], r["rem_s"]), (r["add_t"], r["add_s"])):
            t = yahoo_ticker(tk) if pd.notna(tk) else ""
            if not t or t == "NAN":
                continue
            if pd.notna(r["rem_t"]) and t == yahoo_ticker(r["rem_t"]):
                if t in open_at:
                    done.setdefault(t, []).append(
                        (open_at.pop(t), d, clean_name(nm)))
                else:
                    done.setdefault(t, []).append(("", d, clean_name(nm)))
            else:
                open_at.setdefault(t, d)

    rows, seen = [], set()
    for _, r in cur.iterrows():
        t = yahoo_ticker(r["Symbol"])
        s = open_at.get(t)
        rows.append({
            "symbol": t, "company": clean_name(r["Security"]),
            "start_date": iso(s), "end_date": "",
            "status": "Current", "source": "wikipedia:sp400",
            "note": "" if s else "join date before Jan 2012 not recorded",
        })
        seen.add(t)
    for t, wins in done.items():
        if t in seen:
            continue
        for s, e, nm in wins:
            rows.append({
                "symbol": t, "company": nm, "start_date": iso(s),
                "end_date": iso(e), "status": "Removed", "source": "wikipedia:sp400",
                "note": "" if s else "join date before Jan 2012 not recorded",
            })
    return rows


# --------------------------------------------------------------------------
# Nasdaq-100
# --------------------------------------------------------------------------
def build_nasdaq100():
    wb_path = os.path.join(C.SOURCES, "Nasdaq100_all_constituents.xlsx")
    import openpyxl
    ws = openpyxl.load_workbook(wb_path)["All Constituents"]
    rows = []
    for ticker, company, start, end, status, reason, notes in ws.iter_rows(
            min_row=2, values_only=True):
        if not ticker:
            continue
        t = yahoo_ticker(ticker)
        st = str(status or "").strip()
        startd, endd = parse_date(start), parse_date(end)

        # 'before 27.01.1995' is an upper bound, not a join date: the symbol was
        # already a member by then. Keep the bound in the note rather than
        # inventing a start date.
        note = clean_name(notes or "")
        if not startd and start and str(start).lower().startswith("before"):
            note = (note + "; " if note else "") + f"member from before {start[7:]}"
        if st == "Removed" and not endd:
            note = (note + "; " if note else "") + "removal date not recorded"
            st = "Removed (date unknown)"
        rows.append({
            "symbol": t, "company": clean_name(company or ""),
            "start_date": iso(startd), "end_date": iso(endd),
            "status": "Current" if (st == "Current") else st,
            "source": "Nasdaq100_all_constituents.xlsx",
            "note": note,
        })
    return rows


# --------------------------------------------------------------------------
# Dow Jones Industrial Average
# --------------------------------------------------------------------------
def github_djia_names():
    """company name -> ticker, taken from the GitHub source itself.

    Wikipedia writes a company under its CURRENT name (AlliedSignal is shown as
    Honeywell, Eastman Kodak as Kodak), while the GitHub dataset uses the ticker
    that was live at the time (ALD, EK, HWP). Deriving the map from the GitHub
    file keeps both sides on the same convention, so a rename cannot masquerade
    as a membership disagreement.
    """
    p = os.path.join(C.SOURCES, "github_djia", "dow30_history.csv")
    return {clean_name(r["name"]): yahoo_ticker(r["symbol"])
            for r in csv.DictReader(open(p, encoding="utf-8-sig"))}


def djia_snapshots():
    """[(date, set(tickers))] for every change-date section, oldest first."""
    gh_names = github_djia_names()
    s = soup_of(os.path.join(C.WIKI_RAW, "dowjones_live.html"))
    body = s.find("div", {"class": "mw-parser-output"})
    snaps = []
    for h in body.find_all(["h2", "h3"]):
        hid = h.get("id", "")
        if not re.match(r"^[A-Z][a-z]+_\d{1,2},_\d{4}$", hid):
            continue
        d = datetime.datetime.strptime(hid.replace("_", " "), "%B %d, %Y").date()
        node = h.parent if h.parent.name == "div" else h
        tbl = None
        for sib in node.find_next_siblings():
            if sib.name == "table":
                tbl = sib
                break
            if sib.name in ("h2", "h3"):
                break
        if tbl is None:
            continue
        df = pd.read_html(io.StringIO(str(tbl)))[0]
        # Layout is 30 component cells, then a "Dropped from Average" marker row
        # followed by the company/companies that just left. Everything from the
        # marker down is commentary, not membership, so stop there.
        tick, unmapped = set(), set()
        stop = False
        for _, row in df.iterrows():
            cells = [str(v) for v in row.values if str(v) != "nan"]
            if any("Dropped" in c for c in cells):
                stop = True
            if stop:
                continue
            for v in cells:
                n = clean_name(v)
                if not n or n == "nan" or "Added" in n:
                    continue
                t = gh_names.get(n) or djia_names.ticker_for(n)
                if t:
                    tick.add(t)
                else:
                    unmapped.add(n)
        snaps.append((d, tick, unmapped))
    snaps.sort(key=lambda x: x[0])
    return snaps


def djia_from_wikipedia():
    """Independent reconstruction, used only to cross-check the GitHub source."""
    snaps = djia_snapshots()
    # keep 1990 coverage: the 1987 snapshot is the composition entering 1990
    snaps = [s for s in snaps if s[0] >= datetime.date(1987, 1, 1)]
    if not snaps:
        return []

    base_d, base, _ = snaps[0]
    open_at = {t: base_d for t in base}
    windows = {}
    all_unmapped = set()
    for d, tick, unmapped in snaps:
        all_unmapped |= unmapped
    for i in range(1, len(snaps)):
        d, tick, _ = snaps[i]
        prev = snaps[i - 1][1]
        for t in tick - prev:               # added on d
            open_at.setdefault(t, d)
        for t in prev - tick:               # removed on d
            windows.setdefault(t, []).append((open_at.pop(t, ""), d))

    rows = []
    for t in sorted(set(open_at) | set(windows)):
        if t in open_at:
            rows.append({"symbol": t, "company": "", "start_date": iso(open_at[t]),
                         "end_date": "", "status": "Current",
                         "source": "wikipedia:DJIA snapshots"})
        for s, e in windows.get(t, []):
            rows.append({"symbol": t, "company": "", "start_date": iso(s),
                         "end_date": iso(e), "status": "Removed",
                         "source": "wikipedia:DJIA snapshots"})
    if all_unmapped:
        print(f"  [warn] DJIA names with no ticker mapping: {sorted(all_unmapped)}")
    return rows


def djia_official_pre1991():
    """Pre-1991 DJIA members, from S&P DJI's own historical-components PDF.

    The GitHub source carries no rows at all for the companies removed on
    1991-05-06 (Navistar, Primerica, USX), so without this the 1990-1991
    composition is three members short. The PDF's 1988-12-16 roster is the
    composition that was in force right up to that change, so each name in it
    that the GitHub data does not already cover becomes a span ending
    1991-05-06.

    Returns [(symbol, company, start, end)] or [] if the PDF is unavailable.
    """
    path = os.path.join(C.SOURCES, "independent", "spdji_djia_history.pdf")
    if not os.path.exists(path):
        return []
    try:
        import pypdf
    except ImportError:
        return []

    text = "\n".join(p.extract_text() for p in pypdf.PdfReader(path).pages)
    start_i = text.find("December 16, 1988")
    end_i = text.find("May 6, 1991")
    if start_i < 0 or end_i < 0 or end_i <= start_i:
        return []

    names = []
    for line in text[start_i:end_i].split("\n"):
        s = line.strip()
        # drop the heading, the dated change notes, and anything prose-like
        if not s or s.startswith("December") or "–" in s or "changes occurred" in s:
            continue
        if len(s) > 42 or s.endswith(".") and len(s.split()) > 4:
            continue
        names.append(s)

    return names


def build_dowjones():
    """DJIA from unliftedq/index-constitution, cross-checked against Wikipedia.

    The GitHub dataset is ticker-based with dated join/leave spans, so it needs
    no name->ticker mapping. The Wikipedia snapshots are reconstructed
    independently and the two current rosters are compared: agreement between
    two unrelated sources is the strongest evidence available that the
    membership is right.
    """
    path = os.path.join(C.SOURCES, "github_djia", "dow30_history.csv")
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        sym = yahoo_ticker(r["symbol"])
        opt_in = parse_date(r["opt-in"])
        opt_out = parse_date(r["opt-out"])
        if opt_out and opt_out < datetime.date(1990, 1, 1):
            continue                       # left before the requested window
        rows.append({
            "symbol": sym, "company": clean_name(r["name"]),
            "start_date": iso(opt_in), "end_date": iso(opt_out),
            "status": "Removed" if opt_out else "Current",
            "source": "github:unliftedq/index-constitution",
        })

    # ---- fill the pre-1991 hole from the official provider document ----
    # Rule: if the official 1988-12-16 roster names a company that our data does
    # NOT already place in the index on that date, add the span. Mapping goes
    # through the GitHub spellings first, so "Eastman Kodak" resolves to EK (the
    # ticker the rest of the data uses) rather than today's KODK.
    D0, D1 = datetime.date(1988, 12, 16), datetime.date(1991, 5, 6)
    gh_names = github_djia_names()

    def _norm(s):
        stop = {"the", "company", "companies", "incorporated", "inc",
                "corporation", "corp", "group", "holdings", "stores", "mfg",
                "and", "co", "plc", "ltd", "international"}
        s = re.sub(r"[^a-z0-9 ]", " ", str(s).lower())
        return "".join(t for t in s.split() if t not in stop)

    norm_map = {}
    for n, t in gh_names.items():
        norm_map.setdefault(_norm(n), t)
    for n, t in djia_names.NAME_TO_TICKER.items():
        norm_map.setdefault(_norm(n), t)

    have_at = {}
    for r in rows:
        have_at.setdefault(r["symbol"], []).append(
            (r["start_date"], r["end_date"]))
    added_pre = 0
    for name in djia_official_pre1991():
        t = norm_map.get(_norm(name))
        if not t:
            continue
        covered = any((not a or a <= D0.isoformat())
                      and (not b or b > D0.isoformat())
                      for a, b in have_at.get(t, []))
        if covered:
            continue
        have_at.setdefault(t, []).append((D0.isoformat(), D1.isoformat()))
        rows.append({
            "symbol": t, "company": clean_name(name),
            "start_date": D0.isoformat(), "end_date": D1.isoformat(),
            "status": "Removed", "source": "spdji:official history PDF",
            "note": "in the 1988-12-16 roster; missing from the GitHub dataset",
        })
        added_pre += 1
    if added_pre:
        print(f"  [fill]   {added_pre} pre-1991 DJIA members restored from the "
              f"official S&P DJI PDF")

    # ---- cross-check against the Wikipedia reconstruction ----
    try:
        wiki = djia_from_wikipedia()
        gh_cur = {r["symbol"] for r in rows if r["status"] == "Current"}
        wk_cur = {r["symbol"] for r in wiki if r["status"] == "Current"}
        if gh_cur == wk_cur:
            print(f"  [cross-check] DJIA current roster agrees across both "
                  f"sources ({len(gh_cur)} members)")
        else:
            print(f"  [cross-check] DJIA CURRENT ROSTER DISAGREES: "
                  f"github-only={sorted(gh_cur-wk_cur)} "
                  f"wikipedia-only={sorted(wk_cur-gh_cur)}")
        gh_all = {r["symbol"] for r in rows}
        wk_all = {r["symbol"] for r in wiki}
        print(f"  [cross-check] ever-members: github {len(gh_all)}, "
              f"wikipedia {len(wk_all)}, "
              f"shared {len(gh_all & wk_all)}")
    except Exception as e:                                       # noqa: BLE001
        print(f"  [cross-check] skipped: {type(e).__name__}: {str(e)[:60]}")
    return rows


# --------------------------------------------------------------------------
def canonicalise_spans():
    """Union strictly-overlapping dated spans per symbol, and log every merge."""
    audit = []
    for slug in C.INDEX_SLUGS:
        path = os.path.join(C.INDICES, slug, "membership.csv")
        if not os.path.exists(path):
            continue
        rows = list(csv.DictReader(open(path, encoding="utf-8")))
        per = {}
        for r in rows:
            if r["symbol"]:
                per.setdefault(r["symbol"], []).append(r)
        out, changed = [], False
        for sym, rs in per.items():
            dated = [r for r in rs if r["start_date"] and r["end_date"]]
            rest = [r for r in rs if r not in dated]
            dated.sort(key=lambda r: (r["start_date"], r["end_date"]))
            merged = []
            for r in dated:
                if merged and r["start_date"] < merged[-1]["end_date"]:
                    keep = merged[-1]
                    audit.append({
                        "index_slug": slug, "symbol": sym,
                        "kept": f"{keep['start_date']}..{keep['end_date']}",
                        "absorbed": f"{r['start_date']}..{r['end_date']}",
                        "reason": "the two spans overlap; the union is one "
                                  "continuous membership"})
                    if r["end_date"] > keep["end_date"]:
                        keep["end_date"] = r["end_date"]
                        keep["status"] = ("Removed" if keep["end_date"] else "Current")
                    keep["note"] = ((keep["note"] + "; ") if keep["note"] else "") + \
                        f"merged with an overlapping span {r['start_date']}..{r['end_date']}"
                    changed = True
                else:
                    merged.append(dict(r))
            out.extend(merged + rest)
        if changed:
            write_membership(slug, out)
    if audit:
        ap = os.path.join(C.INDICES, "span_merges.csv")
        with open(ap, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=[
                "index_slug", "symbol", "kept", "absorbed", "reason"])
            w.writeheader()
            w.writerows(audit)
        print(f"  merged {len(audit)} overlapping spans -> {ap}")


def main():
    os.makedirs(C.INDICES, exist_ok=True)
    builders = {
        "sp100": lambda: fix_sp100_recent(
            build_from_list("sp100", "sp100_user_list.txt")),
        "nasdaqbiotech": lambda: build_from_list("nasdaqbiotech", "nbi_user_list.txt"),
        "russell1000": lambda: build_from_list("russell1000", "russell1000_user_list.txt"),
        "russell2000": lambda: build_from_list("russell2000", "russell2000_user_list.txt"),
        "russell3000": lambda: build_from_list("russell3000", "russell3000_user_list.txt"),
        "sp500": build_sp500,
        "spmidcap400": build_spmidcap400,
        "nasdaq100": build_nasdaq100,
        "dowjones": build_dowjones,
        # a custom group, not an index: same machinery, supplied list is the source
        "etf311": lambda: build_from_list("etf311", "etf311_user_list.txt"),
        # Generated by 15_fetch_etf_liquidity.py; see INDEX_NAMES in config.py.
        "etf500": lambda: build_from_list("etf500", "etf500_user_list.txt"),
    }
    print("membership histories")
    for slug, fn in builders.items():
        rows = fn()
        write_membership(slug, rows)

    # The merge step is called here rather than left to the caller, because
    # forgetting it is silent: the S&P 500 builds from 1996 on, so a rebuild
    # without the 1990-1996 fills simply produces a SHORTER history that still
    # passes every check. That happened - 642 rows across sp500, nasdaq100 and
    # spmidcap400 were dropped by a re-run of 20 that did not re-run 25, and
    # only a full regenerate-and-diff caught it.
    merge = importlib.import_module("data_providers.warehouse.25_merge_external_lists")
    merge.main()

    # Finally, canonicalise: a symbol must not hold two spans that overlap in
    # the same index. The sources occasionally produce them (the S&P 500 build
    # has CBS twice from 1957, once via Wikipedia and once via the supplied
    # list, and FISV as both a closed and an open span from 2001) and a roster
    # comparison that counts distinct symbols cannot see it. Only spans with
    # BOTH dates are merged: a blank date is "unknown" and merges with nothing.
    canonicalise_spans()


if __name__ == "__main__":
    main()
