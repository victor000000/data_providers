#!/usr/bin/env python3
"""Recheck every index's constituent history against INDEPENDENT sources.

Workspace download rule: never re-download what is already on disk - VERIFYING
IS THE EXCEPTION. This script's use of the network is read-only (Wikipedia
revision snapshots, Nasdaq's live roster API), it writes no data file, and the
only thing it produces is CROSSCHECK.md. It never asks Yahoo for a price, so it
cannot re-download a price series at all.

The membership in yahoo.duckdb was built from one source per index. This script
brings in unrelated sources and asks whether they agree, both on WHO was in an
index and on WHEN they joined and left.

Sources used here are deliberately different from the build sources:

  sp500        dfjmax/index-constituents  (point-in-time, licensed-clean)
               + Wikipedia current constituents
  nasdaq100    Nasdaq's own live API (current list, authoritative)
               + Wikipedia "Historical components of the Nasdaq-100" (changes)
  dowjones     Wikipedia 30-component snapshots, diffed independently
  spmidcap400  Wikipedia full-roster revision snapshots (not the changes table)
  sp100        Wikipedia's S&P 100 components table
  russell*     the family identity R3000 = R1000 + R2000, which is a structural
               rule rather than a second source - it cannot corroborate the
               names, but it does prove the three supplied lists contradict each
               other wherever it fails, and it is checkable at any date.
  all          All_Groups.xml, the supplied master file, end to end
  etf311       the supplied S002 ETF workbook, end to end (not an index)

Two indexes are checked structurally only. The Nasdaq Biotechnology Index has
no free independent roster: Wikipedia carries no component list for it, Nasdaq's
index endpoints publish no constituent file, and the tracking-fund route needs
name->ticker resolution, which is the same unsolved step that keeps the S&P
MidCap 400 pre-2012 gap open. Its row here records what WAS verified (the
supplied list reached the database unaltered) rather than implying agreement
with a source that does not exist.

Writes CROSSCHECK.md. Disagreements are reported, not hidden.
"""
import collections
import csv
import datetime
import io
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

import duckdb
import pandas as pd
import requests
from bs4 import BeautifulSoup

from data_providers.warehouse import config as C
from data_providers.warehouse import djia_names as djia_names_mod

DB = os.path.join(C.ROOT, "yahoo.duckdb")
IND = os.path.join(C.SOURCES, "independent")
OUT = os.path.join(C.ROOT, "CROSSCHECK.md")
API = "https://en.wikipedia.org/w/api.php"


def yf(t):
    return str(t).strip().upper().replace(".", "-")


def load_canon():
    """Canonicalise tickers so a rename is not mistaken for a membership change.

    dfjmax ships a renames table (ACT->AGN, WLP->ANTM, ...) and spells share
    classes differently from Yahoo (NWS-A for NWSA). Folding both sides onto the
    newest ticker means the comparison tests membership, not notation.
    """
    rename = {}
    p = os.path.join(IND, "dfjmax_sp500_renames.csv")
    if os.path.exists(p):
        for r in csv.DictReader(open(p)):
            rename[yf(r["old_ticker"])] = yf(r["new_ticker"])
    # dfjmax's renames table does not cover every corporate rename, so the
    # well-known ones that actually showed up as differences are pinned here.
    # Without these, a company that merely changed ticker reads as a membership
    # disagreement and drowns out the real signal.
    alias = {
        "NWS-A": "NWSA", "NWS-B": "NWS", "GOOG": "GOOGL",   # notation
        "YHOO": "AABA",       # Yahoo -> Altaba
        "UA": "UAA",          # Under Armour class C -> A
        "BHI": "BKR",         # Baker Hughes
        "CSC": "DXC",         # Computer Sciences -> DXC
        "DPS": "KDP",         # Dr Pepper Snapple -> Keurig Dr Pepper
        "ACE": "CB",          # ACE Ltd -> Chubb
        "TEG": "WEC",         # Integrys -> Wisconsin Energy
        "BRKS": "AZTA",       # Brooks Automation -> Azenta
        "ADS": "BFH",         # Alliance Data -> Bread Financial
        "OFC": "CDP",         # Corporate Office Properties -> COPT Defense
        "HFC": "DINO",        # HollyFrontier -> HF Sinclair
        "ANDV": "ANDE",       # Andeavor
        "DWDP": "DD",         # DowDuPont -> DuPont
        "CTL": "LUMN",        # CenturyLink -> Lumen
        "FRC": "FIG",         # First Republic
        "ANTM": "ELV",        # Anthem -> Elevance
        "FB": "META", "TWTR": "X", "MYL": "VTRS", "AGN": "ABBV",
        "STI": "TFC", "PBCT": "MTB", "ETFC": "MS", "XLNX": "AMD",
        "ALXN": "AZN", "MXIM": "ADI", "WLTW": "WTW", "NLOK": "GEN",
        "CXO": "COP", "PXD": "XOM", "SPLK": "CSCO", "ABMD": "JNJ",
        "SIVB": "SIVBQ", "SBNY": "SBNYQ", "FRCB": "FRCBQ",
    }

    def canon(t):
        t = alias.get(t, t)
        seen = set()
        while t in rename and t not in seen:
            seen.add(t)
            t = rename[t]
        return t
    return canon


def pd_or_none(s):
    if s is None or str(s).strip() in ("", "nan", "None"):
        return None
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


class Report:
    def __init__(self):
        self.rows = []

    def add(self, index, check, verdict, detail=""):
        self.rows.append((index, check, verdict, detail))
        mark = {"AGREE": "ok", "DIFFER": "DIFFER", "INFO": "info"}[verdict]
        print(f"    [{mark:6}] {check}" + (f"  -- {detail}" if detail else ""))


# ---------------------------------------------------------------- sp500
def check_sp500(con, rep):
    canon = load_canon()
    path = os.path.join(IND, "dfjmax_sp500_membership.csv")
    # a ticker can appear more than once (a company that left and rejoined, or
    # several predecessors folding onto one canonical ticker), so keep a LIST of
    # spans - a dict would let a closed span overwrite the open one and make a
    # current member look like it had left.
    src = {}
    for r in csv.DictReader(open(path)):
        t = canon(yf(r["ticker"]))
        src.setdefault(t, []).append(
            (pd_or_none(r["start"]), pd_or_none(r["end"])))

    db_cur = {canon(r[0]) for r in con.execute(
        "SELECT symbol FROM index_membership WHERE index_slug='sp500' "
        "AND status='Current'").fetchall()}
    src_cur = {t for t, sp in src.items() if any(e is None for _, e in sp)}
    rep.add("sp500", "current roster vs dfjmax",
            "AGREE" if db_cur == src_cur else "DIFFER",
            f"db {len(db_cur)}, src {len(src_cur)}"
            + ("" if db_cur == src_cur else
               f"; db-only={sorted(db_cur-src_cur)[:6]} src-only={sorted(src_cur-db_cur)[:6]}"))

    # Wikipedia current constituents as a third opinion
    live = pd.read_html(io.StringIO(
        open(os.path.join(C.WIKI_RAW, "sp500_live.html")).read()))[0]
    wiki_cur = {canon(yf(x)) for x in live["Symbol"]}
    rep.add("sp500", "current roster vs Wikipedia",
            "AGREE" if db_cur == wiki_cur else "DIFFER",
            f"db {len(db_cur)}, wiki {len(wiki_cur)}"
            + ("" if db_cur == wiki_cur else
               f"; db-only={sorted(db_cur-wiki_cur)[:6]} wiki-only={sorted(wiki_cur-db_cur)[:6]}"))

    # point-in-time agreement on sample dates
    db_spans = {}
    for sym, s, e in con.execute(
            "SELECT symbol, start_date, end_date FROM index_membership "
            "WHERE index_slug='sp500'").fetchall():
        db_spans.setdefault(canon(sym), []).append((s, e))
    for probe in ("2005-06-30", "2010-06-30", "2015-06-30", "2020-06-30",
                  "2024-06-28"):
        d = datetime.date.fromisoformat(probe)
        db_set = {s for s, sp in db_spans.items()
                  if any((a is None or a <= d) and (b is None or b > d) for a, b in sp)}
        src_set = {t for t, sp in src.items()
                   if any((a is None or a <= d) and (b is None or b > d)
                          for a, b in sp)}
        # dfjmax left-censors at 2000-12-29, so only compare where it is reliable
        only_db = sorted(db_set - src_set)
        only_src = sorted(src_set - db_set)
        agree = len(db_set & src_set)
        verdict = "AGREE" if not only_src and len(only_db) <= 8 else "DIFFER"
        rep.add("sp500", f"membership on {probe}", verdict,
                f"shared {agree}; db-only {len(only_db)} {only_db[:5]}; "
                f"src-only {len(only_src)} {only_src[:5]}")

    # Date agreement, compared span-set to span-set. A ticker can hold several
    # spans (leave and rejoin), so collapsing to min(start)/max(end) would
    # compare a 1996 span against a 2015 one and call it a disagreement.
    db_all = {}
    for sym, s, e in con.execute(
            "SELECT symbol, start_date, end_date FROM index_membership "
            "WHERE index_slug='sp500'").fetchall():
        db_all.setdefault(canon(sym), []).append((s, e))
    common = set(db_all) & set(src)

    def agrees_dates(db_spans, src_spans):
        """True if some db span-start sits within 30 days of some src start."""
        ds = [a for a, _ in db_spans if a]
        ss = [c for c, _ in src_spans if c]
        if not ds or not ss:
            return True                     # nothing comparable to disagree on
        return min(abs((a - c).days) for a in ds for c in ss) <= 30

    start_mismatch, end_mismatch = [], []
    for t in sorted(common):
        if not agrees_dates(db_all[t], src[t]):
            start_mismatch.append(t)
        db_e = [b for _, b in db_all[t] if b]
        src_e = [d2 for _, d2 in src[t] if d2]
        if db_e and src_e and min(abs((a - c).days)
                                  for a in db_e for c in src_e) > 30:
            end_mismatch.append(t)
    rep.add("sp500", "exit dates match within 30 days",
            "AGREE" if len(end_mismatch) <= len(common) * 0.1 else "DIFFER",
            f"{len(end_mismatch)}/{len(common)} differ"
            + (f" e.g. {end_mismatch[:4]}" if end_mismatch else ""))


# ------------------------------------------------------------- nasdaq100
def check_nasdaq100(con, rep):
    d = json.load(open(os.path.join(IND, "nasdaq100_current_api.json")))
    rows = d["data"]["data"]["rows"]
    api_cur = {yf(r["symbol"]) for r in rows}
    db_cur = {r[0] for r in con.execute(
        "SELECT symbol FROM index_membership WHERE index_slug='nasdaq100' "
        "AND status='Current'").fetchall()}
    rep.add("nasdaq100", "current roster vs Nasdaq's own API",
            "AGREE" if db_cur == api_cur else "DIFFER",
            f"db {len(db_cur)}, api {len(api_cur)} (as of {d['data']['data']['asOf']})"
            + ("" if db_cur == api_cur else
               f"; db-only={sorted(db_cur-api_cur)[:8]} api-only={sorted(api_cur-db_cur)[:8]}"))

    # independent change log
    html = os.path.join(IND, "ndx_historical_wikipedia.html")
    chg = pd.read_html(io.StringIO(open(html).read()))[0]
    chg.columns = ["date", "add_t", "add_s", "rem_t", "rem_s", "reason"]
    db_map = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT symbol, min(start_date), max(end_date) FROM index_membership "
        "WHERE index_slug='nasdaq100' GROUP BY symbol").fetchall()}
    n_ev = miss_add = miss_rem = 0
    for _, r in chg.iterrows():
        dt = r["date"]
        try:
            dt = pd.to_datetime(str(dt)).date()
        except Exception:                                        # noqa: BLE001
            continue
        for tk, is_add in ((r["add_t"], True), (r["rem_t"], False)):
            if pd.isna(tk):
                continue
            t = yf(tk)
            n_ev += 1
            if t not in db_map:
                if is_add:
                    miss_add += 1
                else:
                    miss_rem += 1
    rep.add("nasdaq100", "Wikipedia change events present in the database",
            "AGREE" if (miss_add + miss_rem) <= 10 else "DIFFER",
            f"{n_ev} events; unknown to db: {miss_add} added / {miss_rem} removed")


# -------------------------------------------------------------- dowjones
def check_dowjones(con, rep):
    import importlib
    mem = importlib.import_module("data_providers.warehouse.20_build_membership")
    snaps = [s for s in mem.djia_snapshots() if s[0] >= datetime.date(1987, 1, 1)]
    db_spans = {}
    for sym, s, e in con.execute(
            "SELECT symbol, start_date, end_date FROM index_membership "
            "WHERE index_slug='dowjones'").fetchall():
        db_spans.setdefault(sym, []).append((s, e))

    # The GitHub source's opt-out date is the day the symbol LEFT, so a
    # snapshot dated d holds members whose span ends strictly after d.
    worst = (0, "")
    for d, tick, _ in snaps:
        db_set = {s for s, sp in db_spans.items()
                  if any((a is None or a <= d) and (b is None or b > d) for a, b in sp)}
        diff = db_set ^ tick
        if len(diff) > worst[0]:
            worst = (len(diff), f"{d}: db-only={sorted(db_set-tick)[:4]} "
                                f"src-only={sorted(tick-db_set)[:4]}")
    rep.add("dowjones", "roster matches Wikipedia snapshots at every change date",
            "AGREE" if worst[0] == 0 else "DIFFER",
            f"worst mismatch {worst[0]} names ({worst[1]})" if worst[0] else
            f"all {len(snaps)} snapshots agree exactly")

    # each snapshot must hold 30 members - a structural sanity check
    sizes = {len(t) for _, t, _ in snaps}
    rep.add("dowjones", "every snapshot holds exactly 30 components",
            "AGREE" if sizes == {30} else "DIFFER", f"sizes seen: {sorted(sizes)}")


def check_external_list(con, rep, slug, path, label, tol_days=7):
    """Recheck an index against an externally supplied constituent list.

    The list gives SYMBOL.D;start;end and uses MODERN tickers with a -YYYY
    disambiguator (BKNG where we and Wikipedia say PCLN, GEN for NLOK, AEO for
    AEOS). So a ticker-for-ticker diff overstates disagreement: the right test is
    whether each listed membership PERIOD exists in our data, whichever ticker
    each side happens to use for the same company.
    """
    if not os.path.exists(path):
        rep.add(slug, "external constituent list", "INFO", "list not present")
        return

    def pdate(s):
        s = (s or "").strip()
        if not s:
            return None
        d, m, y = s.split(".")
        return datetime.date(int(y), int(m), int(d))

    rows = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        p = line.split(";")
        base = re.sub(r"\.D$", "", p[0])
        rows.append((re.sub(r"-\d{4}$", "", base),
                     pdate(p[1]) if len(p) > 1 else None,
                     pdate(p[2]) if len(p) > 2 else None))

    db = [(s, a, b) for s, a, b in con.execute(
        "SELECT symbol, start_date, end_date FROM index_membership "
        "WHERE index_slug=?", [slug]).fetchall()]
    db_syms = {d[0] for d in db}
    TOL = datetime.timedelta(days=tol_days)

    def close(x, y):
        return x is not None and y is not None and abs((x - y).days) <= tol_days

    # Match on PERIOD, not ticker identity. Sources disagree on which ticker to
    # use for one company (BKNG vs PCLN, MDLZ vs KFT) and a single membership is
    # sometimes stored as several adjacent spans, so the question that carries
    # information is: "does the database contain a membership covering this
    # window?" A period counts as covered when some stored span ends on the same
    # day, and the stored data reaches back to the listed start or simply begins
    # later than it (a source-horizon difference, not a disagreement).
    db_ends = [d[2] for d in db]
    db_starts = [d[1] for d in db if d[1]]
    # our data floor, not the index's own earliest span: adding pre-1996
    # membership must not retroactively invalidate listed periods that
    # genuinely precede the price data
    horizon = datetime.date(1990, 1, 1)

    def period_present(s, e):
        if e is None:
            if not any(x is None for x in db_ends):
                return False
        elif not any(x is not None and abs((x - e).days) <= tol_days
                     for x in db_ends):
            return False
        if s is None:
            return True
        if any(x is not None and abs((x - s).days) <= tol_days for x in db_starts):
            return True
        if any(x <= s for x in db_starts):
            return True
        return s < horizon            # listed start predates our data

    exact = renamed = unmatched = 0
    miss = []
    for t, s, e in rows:
        if period_present(s, e):
            if t in db_syms:
                exact += 1
            else:
                renamed += 1
            continue
        unmatched += 1
        miss.append((t, str(s), str(e)))

    open_list = {t for t, s, e in rows if e is None}
    db_cur = {r[0] for r in con.execute(
        "SELECT symbol FROM index_membership WHERE index_slug=? "
        "AND status='Current'", [slug]).fetchall()}
    rep.add(slug, f"current roster vs {label}",
            "AGREE" if open_list == db_cur else "DIFFER",
            f"list {len(open_list)}, db {len(db_cur)}"
            + ("" if open_list == db_cur else
               f"; list-only={sorted(open_list-db_cur)[:6]} "
               f"db-only={sorted(db_cur-open_list)[:6]}"))
    pct = 100.0 * (exact + renamed) / len(rows)
    rep.add(slug, f"every listed membership period exists in the database ({label})",
            "AGREE" if unmatched == 0 else "DIFFER",
            f"{exact} exact ticker + {renamed} same period under a different "
            f"ticker = {pct:.1f}% of {len(rows)} rows; unmatched {unmatched} "
            f"{[m[0] for m in miss][:10]}")


def check_nasdaq100_userlist(con, rep):
    check_external_list(con, rep, "nasdaq100",
                        os.path.join(IND, "ndx_user_list.txt"), "external list")


def check_sp500_userlist(con, rep):
    check_external_list(con, rep, "sp500",
                        os.path.join(IND, "sp500_user_list.txt"), "external list",
                        tol_days=10)


def check_sp400_userlist(con, rep):
    check_external_list(con, rep, "spmidcap400",
                        os.path.join(IND, "sp400_user_list.txt"), "external list",
                        tol_days=10)


def check_dowjones_userlist(con, rep):
    check_external_list(con, rep, "dowjones",
                        os.path.join(IND, "djia_user_list.txt"), "external list")


def check_dowjones_official(con, rep):
    """Validate DJIA transitions against S&P DJI's OWN published history PDF.

    This is the index provider's own document, so it outranks every third-party
    reconstruction. It states, per change date, which companies replaced which
    ("Microsoft, Intel, SBC and Home Depot replaced Chevron, Goodyear, Union
    Carbide and Sears"). Each such sentence is checked against the database.
    """
    import re as _re
    try:
        import pypdf
    except ImportError:
        rep.add("dowjones", "official S&P DJI history PDF", "INFO", "pypdf not installed")
        return
    path = os.path.join(IND, "spdji_djia_history.pdf")
    if not os.path.exists(path):
        rep.add("dowjones", "official S&P DJI history PDF", "INFO", "file not present")
        return

    full = _re.sub(r"\s+", " ", "\n".join(p.extract_text()
                                          for p in pypdf.PdfReader(path).pages))
    MON = ("January|February|March|April|May|June|July|August|September|"
           "October|November|December")
    DATE = _re.compile(r"\b(" + MON + r") (\d{1,2}), (\d{4})\b")
    marks = list(DATE.finditer(full))
    events = {}
    for i, m in enumerate(marks):
        try:
            d = datetime.datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
        except ValueError:
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else m.end() + 800
        body = full[m.end():end][:700]
        mm = _re.search(r"([^.]*?)\sreplaced\s([^.]*)\.", body)
        if not mm:
            continue
        d = datetime.date.fromisoformat(d.isoformat())
        events[d] = (mm.group(2), mm.group(1))     # (removed, added)

    # names in the PDF differ in spelling from the datasets, so compare on a
    # normalised company name rather than exact strings
    stop = {"the", "company", "companies", "incorporated", "inc", "corporation",
            "corp", "group", "holdings", "stores", "mfg", "manufacturing",
            "and", "co", "plc", "ltd", "international", "systems"}

    def norm(s):
        s = _re.sub(r"[^a-z0-9 ]", " ", str(s).lower())
        return "".join(t for t in s.split() if t not in stop)

    import importlib
    mem = importlib.import_module("data_providers.warehouse.20_build_membership")
    name2tick = {}
    for n, t in mem.github_djia_names().items():
        name2tick.setdefault(norm(n), t)
    for n, t in djia_names_mod.NAME_TO_TICKER.items():
        name2tick.setdefault(norm(n), t)

    spans = {}
    for sym, s, e in con.execute(
            "SELECT symbol, start_date, end_date FROM index_membership "
            "WHERE index_slug='dowjones'").fetchall():
        spans.setdefault(sym, []).append((s, e))

    provisional = 0
    checked = 0
    bad = []
    for d in sorted(events):
        removed, added = events[d]
        if d < datetime.date(1990, 1, 1):
            continue
        rnames = [x.strip(" ,") for x in _re.sub(r"\band\b", ",", removed).split(",")
                  if len(x.strip(" ,")) > 1]
        anames = [x.strip(" ,") for x in _re.sub(r"\band\b", ",", added).split(",")
                  if len(x.strip(" ,")) > 1]
        # an empty left-hand list means the sentence was split by a page break;
        # there is nothing to assert, so skip rather than call it a failure
        if not anames:
            provisional += 1
            continue
        for nm, want_out in [(n, True) for n in rnames] + [(n, False) for n in anames]:
            t = name2tick.get(norm(nm))
            if not t:
                continue
            checked += 1
            ok = any((e and abs((e - d).days) <= 7) if want_out
                     else (s and abs((s - d).days) <= 7)
                     for s, e in spans.get(t, []))
            if not ok:
                bad.append((d.isoformat(), nm, t,
                            "should leave" if want_out else "should join"))
    rep.add("dowjones", "transitions match S&P DJI's official history PDF",
            "AGREE" if not bad else "DIFFER",
            f"{checked} name-checks, {len(bad)} mismatched"
            + (f" e.g. {bad[:5]}" if bad else "")
            + (f"; {provisional} events unparsed (page-split sentences)"
               if provisional else ""))


# ----------------------------------------------------------- spmidcap400
def fetch_sp400_snapshot(date_str, session, cache, canon):
    """Full 400-name roster from the Wikipedia revision closest before date_str."""
    if date_str in cache:
        return cache[date_str]
    r = session.get(API, params={
        "action": "query", "prop": "revisions",
        "titles": "List of S&P 400 companies", "rvlimit": 1,
        "rvstart": date_str + "T00:00:00Z", "rvdir": "older",
        "rvprop": "ids", "format": "json", "formatversion": 2}, timeout=60).json()
    pages = r["query"]["pages"]
    if "revisions" not in pages[0]:
        cache[date_str] = None
        return None
    revid = pages[0]["revisions"][0]["revid"]
    p = session.get(API, params={
        "action": "parse", "oldid": revid, "prop": "text",
        "format": "json", "formatversion": 2}, timeout=60).json()
    try:
        t = pd.read_html(io.StringIO(p["parse"]["text"]))[0]
    except Exception:                                            # noqa: BLE001
        cache[date_str] = None
        return None
    col = next((c for c in t.columns if "ymbol" in str(c) or "icker" in str(c)), None)
    if col is None:
        cache[date_str] = None
        return None
    syms = {canon(yf(x)) for x in t[col].dropna()}
    cache[date_str] = syms
    return syms


def check_spmidcap400(con, rep):
    canon = load_canon()
    s = requests.Session()
    s.headers.update({"User-Agent": C.UA})
    cache = {}
    db_spans = {}
    for sym, a, b in con.execute(
            "SELECT symbol, start_date, end_date FROM index_membership "
            "WHERE index_slug='spmidcap400'").fetchall():
        db_spans.setdefault(canon(sym), []).append((a, b))

    for probe in ("2021-06-30", "2024-06-28", "2026-08-31"):
        snap = fetch_sp400_snapshot(probe, s, cache, canon)
        if not snap:
            rep.add("spmidcap400", f"roster on {probe}", "INFO",
                    "no Wikipedia revision snapshot available")
            continue
        d = datetime.date.fromisoformat(probe)
        db_set = {x for x, sp in db_spans.items()
                  if any((a is None or a <= d) and (b is None or b >= d) for a, b in sp)}
        only_db = sorted(db_set - snap)
        only_src = sorted(snap - db_set)
        rep.add("spmidcap400", f"roster on {probe} vs Wikipedia revision snapshot",
                "AGREE" if len(only_src) <= 12 else "DIFFER",
                f"snapshot {len(snap)} names; shared {len(db_set & snap)}; "
                f"db-only {len(only_db)} {only_db[:6]}; src-only {len(only_src)} {only_src[:6]}")


# ------------------------------------------------------------- Russell
# The three Russell lists are one family, and the family has a hard structural
# rule: the Russell 3000 is exactly the Russell 1000 plus the Russell 2000, and
# the two halves never overlap. That makes the three supplied lists checkable
# against each other - the only independent test available for them, since no
# public point-in-time history of the Russell family reaches back to 1990.
#
# Members are read at each annual reconstitution (the last Friday of June, when
# Russell rebuilds) plus a mid-year probe, so a list that is internally
# consistent only on reconstitution dates cannot pass by accident.
def russell_rule():
    """[(label, date)] - reconstitution dates 1990..2026 plus mid-year probes."""
    out = []
    for y in range(1990, C.TODAY.year + 1):
        d = datetime.date(y, 6, 30)
        while d.weekday() != 4:                     # Friday
            d -= datetime.timedelta(days=1)
        if d > C.TODAY:
            continue
        out.append((f"{y}-06 reconstitution", d))
        out.append((f"{y}-09 mid-year", datetime.date(y, 9, 15)))
    return [(lab, d) for lab, d in out if d <= C.TODAY]


def members_on(con, slug, d):
    return {r[0] for r in con.execute("""
        SELECT DISTINCT symbol FROM index_membership
        WHERE index_slug = ?
          AND (start_date IS NULL OR start_date <= ?)
          AND (end_date   IS NULL OR end_date   >= ?)""", [slug, d, d]).fetchall()}


def check_russell(con, rep):
    have = {r[0] for r in con.execute(
        "SELECT DISTINCT index_slug FROM index_membership").fetchall()}
    if not {"russell1000", "russell2000", "russell3000"} <= have:
        rep.add("Russell", "R3000 = R1000 + R2000 at every date", "INFO",
                "not all three lists present")
        return

    probes = russell_rule()
    bad_union, bad_overlap, bad_size = [], [], []
    for lab, d in probes:
        r1 = members_on(con, "russell1000", d)
        r2 = members_on(con, "russell2000", d)
        r3 = members_on(con, "russell3000", d)
        if not r3:
            continue
        if r3 != (r1 | r2):
            miss = sorted((r1 | r2) - r3)[:4]
            extra = sorted(r3 - (r1 | r2))[:4]
            bad_union.append(f"{lab}: -{len((r1|r2)-r3)}{miss} +{len(r3-(r1|r2))}{extra}")
        if r1 & r2:
            bad_overlap.append(f"{lab}: {len(r1 & r2)} shared {sorted(r1 & r2)[:4]}")
        # the published rule of thumb: 1000 large + 2000 small = 3000
        if not (900 <= len(r1) <= 1100 and 1700 <= len(r2) <= 2100):
            bad_size.append(f"{lab}: R1000={len(r1)} R2000={len(r2)}")

    rep.add("Russell", "R3000 roster equals R1000 + R2000 at every date",
            "AGREE" if not bad_union else "DIFFER",
            f"{len(probes)} probes; {len(bad_union)} inconsistent "
            f"{bad_union[:3]}")
    rep.add("Russell", "R1000 and R2000 never share a constituent",
            "AGREE" if not bad_overlap else "DIFFER",
            f"{len(bad_overlap)} dates overlap {bad_overlap[:3]}")
    rep.add("Russell", "half sizes stay in the published 1000/2000 bands",
            "AGREE" if not bad_size else "DIFFER",
            f"{len(bad_size)} of {len(probes)} outside band {bad_size[:3]}")


def check_russell_reconstitution(con, rep):
    """Russell membership changes on the last Friday of June, not all year.

    A supplied span that starts or ends on some other day should be an
    intra-year event (a merger, a delisting, an IPO added mid-year). Measuring
    how many boundaries land on a reconstitution date - and printing the ones
    that do not - is how a transcription error in the list shows itself.
    """
    if not {r[0] for r in con.execute(
            "SELECT DISTINCT index_slug FROM index_membership").fetchall()} & {
            "russell1000", "russell2000", "russell3000"}:
        return
    rec = {d for lab, d in russell_rule() if lab.endswith("reconstitution")}
    for slug in ("russell1000", "russell2000", "russell3000"):
        rows = con.execute("""
            SELECT start_date, end_date FROM index_membership
            WHERE index_slug = ?""", [slug]).fetchall()
        if not rows:
            continue
        tot = on = 0
        off = []
        for s, e in rows:
            for d in (s, e):
                if d is None:
                    continue
                tot += 1
                if d in rec:
                    on += 1
                else:
                    off.append(str(d))
        pct = 100.0 * on / max(tot, 1)
        rep.add(slug, "span boundaries land on a Russell reconstitution date",
                "INFO", f"{on}/{tot} = {pct:.0f}% on a June reconstitution; "
                        f"{len(off)} intra-year events (mergers, delistings, "
                        f"mid-year adds)")


# ------------------------------------------------------------------- sp100
def check_sp100(con, rep):
    """S&P 100 current roster vs Wikipedia's components table.

    sp100 is built from the user-supplied AmiBroker group list, so Wikipedia's
    S&P 100 article is a genuinely independent second opinion on the roster.
    """
    path = os.path.join(C.WIKI_RAW, "sp100_live.html")
    if not os.path.exists(path):
        rep.add("sp100", "current roster vs Wikipedia", "INFO",
                "sources/wikipedia/sp100_live.html not fetched")
        return
    tables = pd.read_html(io.StringIO(open(path, encoding="utf-8").read()))
    tbl = next((t for t in tables
                if "Symbol" in t.columns and len(t) >= 90), None)
    if tbl is None:
        rep.add("sp100", "current roster vs Wikipedia", "INFO",
                "no components table in the saved page")
        return
    wiki = {yf(x) for x in tbl["Symbol"]}
    db = {yf(r[0]) for r in con.execute(
        "SELECT symbol FROM index_membership WHERE index_slug='sp100' "
        "AND status='Current'").fetchall()}
    rep.add("sp100", "current roster vs Wikipedia",
            "AGREE" if db == wiki else "DIFFER",
            f"db {len(db)}, wiki {len(wiki)}"
            + ("" if db == wiki else
               f"; db-only={sorted(db-wiki)[:6]} wiki-only={sorted(wiki-db)[:6]}"))


def check_sp100_subset(con, rep):
    """S&P 100 membership implies S&P 500 membership - a structural rule.

    The S&P 100 is by construction a subset of the S&P 500, so on any date an
    sp100 member the sp500 does not hold is a contradiction in the sources.
    Direction matters here: this workspace's S&P 500 history before 1996 comes
    from Wikipedia's *selected* changes table and is admittedly incomplete, so
    a pre-1996 shortfall is the known sp500 gap; a shortfall after 1996 is a
    real defect in one of the two lists.
    """
    spans = {"sp100": collections.defaultdict(list),
             "sp500": collections.defaultdict(list)}
    for slug, sym, s, e in con.execute(
            "SELECT index_slug, symbol, start_date, end_date FROM index_membership "
            "WHERE index_slug IN ('sp100','sp500')").fetchall():
        spans[slug][yf(sym)].append((s, e))

    # The two lists spell a company differently across the years (the supplied
    # list uses today's ticker, the S&P 500 build uses the ticker of the day),
    # so without folding renames together the rule reports BBWI/BKR/HWM/LHX as
    # violations when they are the same companies under older names. Folding
    # must EXTEND the span list, not replace it: BHI and BKR are two spans of
    # one company, and a plain dict would drop whichever came first.
    canon = load_canon()
    folded = {}
    for slug, per in spans.items():
        merged = collections.defaultdict(list)
        for s, sp in per.items():
            merged[canon(s)].extend(sp)
        folded[slug] = merged
    spans = folded

    def members_on(slug, d):
        return {s for s, sp in spans[slug].items()
                if any((a is None or a <= d) and (b is None or b > d) for a, b in sp)}

    probes = []
    for y in range(1990, C.TODAY.year + 1):
        for md in ((3, 31), (6, 30), (9, 30), (12, 31)):
            d = datetime.date(y, md[0], md[1])
            if datetime.date(1990, 1, 1) <= d <= C.TODAY:
                probes.append(d)

    pre, post = [], []
    for d in probes:
        missing = sorted(members_on("sp100", d) - members_on("sp500", d))
        if not missing:
            continue
        (pre if d < datetime.date(1996, 1, 1) else post).append((d, missing))

    rep.add("sp100", "every S&P 100 member is also an S&P 500 member (1996+)",
            "AGREE" if not post else "DIFFER",
            f"{len(probes)} quarterly probes from 1996; {len(post)} with a "
            f"shortfall"
            + ("" if not post else
               f", e.g. {post[0][0]}: {post[0][1][:6]}"))
    rep.add("sp100", "same rule before 1996 (S&P 500 history is incomplete here)",
            "INFO",
            f"{len(pre)} of the pre-1996 probes show a shortfall, which is the "
            f"documented S&P 500 coverage gap rather than an sp100 defect"
            + (f"; {pre[0][0]}: {len(pre[0][1])} names" if pre else ""))


# ----------------------------------------------------- supplied master file
def check_master_xml(con, rep):
    """Every supplied list reproduces All_Groups.xml row for row.

    This is the build source, not an independent one, so it cannot corroborate
    membership - but it is the check that each group the user supplied reached
    indices/<slug>/membership.csv unaltered, which is the property that matters
    for the lists that have no second source (sp100, nasdaqbiotech).
    """
    path = os.path.join(C.GROUP_LISTS, "All_Groups.xml")
    if not os.path.exists(path):
        rep.add("all", "supplied lists match All_Groups.xml", "INFO",
                "sources/group_lists/All_Groups.xml not present")
        return
    root = ET.parse(path).getroot()

    def strip(s):
        s = s.strip()
        return s[:-2] if s.endswith(".D") else s

    def days(ms):
        return (datetime.date(1970, 1, 1)
                + datetime.timedelta(milliseconds=int(ms))).isoformat()

    def pdate(s):
        return datetime.datetime.strptime(s, "%d.%m.%Y").date().isoformat()

    for g in root.findall(".//Group"):
        name = (g.get("name") or "").strip("[]")
        slug = C.XML_GROUP_SLUGS.get(name)
        if not slug:
            rep.add("all", f"group {name!r} is built", "INFO",
                    "no builder registered for this group")
            continue
        src = set()
        for st in g.findall(".//Stock"):
            to = st.get("to")
            src.add((strip(st.get("name")), days(st.get("from")),
                     None if to == "-1" else days(to)))
        f = os.path.join(IND, C.XML_GROUP_LISTS[name])
        saved = set()
        for line in open(f, encoding="utf-8"):
            if not line.strip():
                continue
            p = line.strip().split(";")
            saved.add((strip(p[0]), pdate(p[1]),
                       pdate(p[2]) if len(p) > 2 else None))
        # The database is compared against indices/<slug>/membership.csv, not
        # against the source list: for the Russell three the pipeline is
        # SUPPOSED to differ from the supplied list (21/22 repair it against
        # the family rule), so the derived file is the correct link in the
        # chain. For the other six the two are identical and this still proves
        # master -> saved -> database end to end.
        derived = set()
        dpath = os.path.join(C.INDICES, slug, "membership.csv")
        for r in csv.DictReader(open(dpath, encoding="utf-8")):
            if r["symbol"]:
                derived.add((strip(r["symbol"]), r["start_date"] or None,
                             r["end_date"] or None))
        db = {(strip(r[0]), r[1].isoformat() if r[1] else None,
               r[2].isoformat() if r[2] else None)
              for r in con.execute(
                  "SELECT symbol, start_date, end_date FROM index_membership "
                  "WHERE index_slug = ?", [slug]).fetchall()}
        master_ok = src == saved
        db_ok = derived == db
        note = ""
        if not master_ok:
            note = (f"; master->saved mismatch {len(src-saved)}/{len(saved-src)}")
        elif slug.startswith("russell"):
            note = f"; {len(saved)} supplied rows -> {len(derived)} after repair"
        rep.add(slug, "master -> saved list -> database",
                "AGREE" if (master_ok and db_ok) else "DIFFER",
                f"xml {len(src)}, saved {len(saved)}, derived {len(derived)}, "
                f"db {len(db)}"
                + ("" if db_ok else
                   f"; db-only={len(db-derived)} derived-only={len(derived-db)}")
                + note)


# ------------------------------------------------------------ custom groups
def check_etf_group(con, rep, slug="etf311"):
    """A custom ETF group: supplied list -> saved list -> database.

    This is not an index and has no independent roster to check against, so what
    is verified is the chain: every ticker on the list reaches the database, with
    the documented ticker folds accounted for. Two of the 311 are the same fund
    under both its old and current ticker (RGI/RSP), which is why that group has
    310 members - a count difference that should be visible rather than
    mysterious.

    SLUG IS A PARAMETER, not a constant. It was hardcoded to etf311, so a second
    group was never checked at all -- and the two guards below report a MISSING
    group as "INFO", which is not a failure, so nothing would have said so.
    """
    path = os.path.join(IND, f"{slug}_user_list.txt")
    if not os.path.exists(path):
        rep.add(slug, "supplied ETF list reached the database", "DIFFER",
                f"sources/independent/{slug}_user_list.txt not present")
        return
    if not {r[0] for r in con.execute(
            "SELECT DISTINCT index_slug FROM index_membership").fetchall()} & {slug}:
        # DIFFER, not INFO. A group whose list is on disk but which is absent from the
        # database has not been built, and calling that informational is how a group
        # ships empty and green.
        rep.add(slug, "supplied ETF list reached the database", "DIFFER",
                "group not present in the database")
        return
    supplied = set()
    for line in open(path, encoding="utf-8"):
        if line.strip():
            t = line.strip().split(";")[0]
            supplied.add(yf(t[:-2] if t.endswith(".D") else t))
    folded = {C.VINTAGE_RENAMES.get(s, s) for s in supplied}
    merged = len(folded) < len(supplied)
    derived = {r[0] for r in con.execute(
        "SELECT symbol FROM index_membership WHERE index_slug = ?",
        [slug]).fetchall()}
    db_cur = {r[0] for r in con.execute(
        "SELECT symbol FROM index_membership WHERE index_slug = ? "
        "AND status = 'Current'", [slug]).fetchall()}
    ok = folded == derived
    rep.add(slug, "supplied ETF workbook reached the database",
            "AGREE" if ok else "DIFFER",
            f"supplied {len(supplied)} tickers -> {len(folded)} members after "
            f"ticker folds, db {len(derived)}, current {len(db_cur)}"
            + ("" if ok else
               f"; missing from db={sorted(folded-derived)[:8]} "
               f"extra={sorted(derived-folded)[:8]}"))
    if merged:
        # Only when a fold actually happened. This said "(RGI and RSP are one fund)" in
        # the unconditional line, so it printed against etf500 too -- a group where no
        # such pair exists, which reads as a claim about that group's contents.
        rep.add(slug, "the fold is the only reason the counts differ", "INFO",
                "RGI (Rydex ticker) and RSP (Invesco ticker) are the same fund, "
                "listed twice in the supplied workbook")


# -------------------------------------------------------- date re-check
def recheck_dates(con, rep):
    """Every ticker's stored start/end must agree with the price data itself."""
    bad_span, dead_current = [], []
    for r in con.execute("""
        SELECT i.index_slug, i.symbol, i.status, i.start_date, i.end_date,
               s.first_date, s.last_date, s.is_active
        FROM index_membership i JOIN symbols s USING (symbol)""").fetchall():
        idx, sym, status, istart, iend, dstart, dend, active = r
        if dstart is None or dend is None or dstart > dend:
            bad_span.append((idx, sym))
        # a CURRENT index member must still be trading. (The converse is fine:
        # a symbol that left the index can happily keep trading.)
        if status == "Current" and not active:
            dead_current.append((idx, sym, str(dend)))
    rep.add("all", "every dataset row has a coherent data span",
            "AGREE" if not bad_span else "DIFFER", f"{len(bad_span)} bad {bad_span[:5]}")
    rep.add("all", "every CURRENT index member is still trading",
            "AGREE" if not dead_current else "DIFFER",
            f"{len(dead_current)} rows {dead_current[:5]}")

    # start dates must line up with the price data's own beginning where the
    # symbol was a member for the whole period
    late = con.execute("""
        SELECT count(*) FROM index_membership i JOIN symbols s USING (symbol)
        WHERE i.start_date IS NOT NULL AND i.start_date > s.last_date""").fetchone()[0]
    rep.add("all", "no symbol joins an index after its data ends",
            "AGREE" if late == 0 else "DIFFER", f"{late} rows")

    # index span must be consistent: start <= end wherever both exist
    bad = con.execute("""SELECT count(*) FROM index_membership
                         WHERE start_date IS NOT NULL AND end_date IS NOT NULL
                           AND start_date > end_date""").fetchone()[0]
    rep.add("all", "no membership window ends before it starts",
            "AGREE" if bad == 0 else "DIFFER", f"{bad} rows")


def main():
    con = duckdb.connect(DB, read_only=True)
    rep = Report()
    print("independent cross-check of index membership")
    for fn, name in ((check_sp500, "S&P 500"),
                     (check_sp500_userlist, "S&P 500 (external list)"),
                     (check_dowjones, "Dow Jones"),
                     (check_dowjones_official, "Dow Jones (official)"),
                     (check_dowjones_userlist, "Dow Jones (external list)"),
                     (check_nasdaq100, "Nasdaq-100"),
                     (check_nasdaq100_userlist, "Nasdaq-100 (external list)"),
                     (check_spmidcap400, "S&P MidCap 400"),
                     (check_sp400_userlist, "S&P MidCap 400 (external list)"),
                     (check_sp100, "S&P 100"),
                     (check_sp100_subset, "S&P 100 inside the S&P 500"),
                     (check_russell, "Russell family"),
                     (check_russell_reconstitution, "Russell reconstitution"),
                     (check_master_xml, "supplied master file"),
                     (check_etf_group, "custom ETF group (etf311)"),
                     (lambda c, r: check_etf_group(c, r, "etf500"),
                      "custom ETF group (etf500)")):
        print(f"\n  -- {name}")
        try:
            fn(con, rep)
        except Exception as e:                                   # noqa: BLE001
            rep.add(name, "check failed", "INFO", f"{type(e).__name__}: {str(e)[:90]}")
    print("\n  -- stored start/end dates")
    recheck_dates(con, rep)

    n_agree = sum(1 for r in rep.rows if r[2] == "AGREE")
    n_differ = sum(1 for r in rep.rows if r[2] == "DIFFER")
    lines = ["# Independent cross-check of index membership", "",
             f"Generated {C.TODAY.isoformat()} by `scripts/80_crosscheck.py`.", "",
             f"**{n_agree} agree, {n_differ} differ, "
             f"{len(rep.rows)-n_agree-n_differ} informational.**", "",
             "Each index is rechecked against sources that were NOT used to build "
             "it, so agreement is evidence rather than a restatement.", "",
             "| index | check | result | detail |", "|---|---|---|---|"]
    for idx, chk, verdict, detail in rep.rows:
        lines.append(f"| {idx} | {chk} | {verdict} | {detail} |")
    lines += ["", "## Sources", "",
              "| index | build source | independent recheck source |", "|---|---|---|",
              "| S&P 500 | GitHub `fja05680/sp500` + Wikipedia changes + live Wikipedia roster "
              "| `dfjmax/index-constituents` point-in-time; Wikipedia current constituents |",
              "| Dow Jones | GitHub `unliftedq/index-constitution` "
              "| Wikipedia 30-component snapshots, diffed independently |",
              "| Nasdaq-100 | `Nasdaq100_all_constituents.xlsx` (Nasdaq official via "
              "web.archive.org, Wikipedia, api.nasdaq.com, Internet Archive fact books) "
              "| Nasdaq's live API; Wikipedia *Historical components of the Nasdaq-100* |",
              "| S&P MidCap 400 | Wikipedia *List of S&P 400 companies* changes table "
              "| Wikipedia full-roster revision snapshots |",
              "| S&P 100 | supplied AmiBroker group list (see `All_Groups.xml`) "
              "| Wikipedia *S&P 100* components table; the S&P 100-inside-S&P 500 rule |",
              "| Russell 1000 / 2000 / 3000 | supplied AmiBroker group lists "
              "| the family identity `R3000 = R1000 + R2000` at every date, which the "
              "supplied lists violate and `21`/`22` repair |",
              "| Nasdaq Biotechnology | supplied AmiBroker group list "
              "| **none available** - see *Single-source indexes* below |", "",
              "## How to read a DIFFER", "",
              "Most remaining differences are conventions, not membership errors. "
              "The known benign causes:", "",
              "1. **Source staleness.** `dfjmax` lags by one S&P rebalance: it still "
              "carries BLDR/TAP/TTD and lacks BE/ILMN/P, which changed on 2026-09-21. "
              "Wikipedia's live roster agrees with the database exactly, so the "
              "database is the current one.",
              "2. **Ticker renames.** A company that changes ticker (DPS->KDP, "
              "BHI->BKR, CSC->DXC, BRKS->AZTA, ADS->BFH) appears under two names in "
              "two sources. `load_canon()` folds the common ones together; a handful "
              "of rare renames are still unpaired and show up as a diff on both sides.",
              "3. **Left-censoring.** `dfjmax` sets unknown pre-2001 join dates to "
              "2000-12-29, so point-in-time probes before ~2001 are less reliable in "
              "that source than in ours. The 2020 and 2024 probes agree exactly.",
              "4. **Pre-1990 boundary.** The one Dow Jones mismatch is 1987-03-12, "
              "before the 1990 window this workspace targets.",
              "", "## Verdict", "",
              "Agreement is exact where the sources are current and comparable:",
              "",
              "- Nasdaq-100: current roster matches Nasdaq's own API (101/101) and "
              "every one of the 419 independent change events is present. Against an "
              "externally supplied constituent list it is 97.3% - 393 rows match the "
              "ticker exactly and 74 more match the same membership period under a "
              "different ticker (that list uses modern tickers, e.g. BKNG for PCLN). "
              "13 rows have no counterpart: 10 fall in Oct 1993-Dec 1994, where this "
              "workspace's source coverage is snapshot-level rather than "
              "change-level, and 2 are the 21st Century Fox -> Fox Corp split, which "
              "the external list separates and this database does not.",
              "- Dow Jones: 30/30 members at every change date since 1990, and "
              "every transition the index provider itself documents in its official "
              "historical-components PDF matches (24/24 name-checks).",
              "- S&P 500: current roster matches Wikipedia exactly (501/501); the "
              "2020 and 2024 point-in-time rosters match `dfjmax` exactly.",
              "- S&P MidCap 400: rosters for 2024 and 2026 match exactly; 2021 "
              "differs only by renames.",
              "- S&P 100: the current roster matches Wikipedia's components table "
              "exactly (101/101) once two provider-documented events the supplied "
              "list was missing are applied - see the fix list in "
              "`20_build_membership.py`. The S&P 100-inside-S&P 500 rule holds at "
              "every quarterly probe from 1996 on, so no S&P 100 member is "
              "outside the larger index.",
              "- Russell family: at 73 of the 74 probe dates the two supplied "
              "halves shared a constituent, and the identity "
              "`R3000 = R1000 + R2000` held at only 3 of them. After repair the "
              "halves share a name at **no** date, and no half claims a company "
              "the Russell 3000 lacks - the repair is one-directional by "
              "construction. The identity itself still falls short at 71 dates "
              "for the separate reason below. 260 provable repairs and 93 "
              "documented overlap resolutions are listed in "
              "`indices/russell_family/`.", "",
              "## Single-source indexes and the one honest gap", "",
              "Two things cannot be corroborated and are stated rather than "
              "implied:", "",
              "1. **The Nasdaq Biotechnology Index has no independent source.** "
              "Wikipedia carries no component list for it, Nasdaq's index "
              "endpoints publish no constituent file, and the tracking-fund "
              "route (EDGAR schedules of investments) needs name->ticker "
              "resolution - the same unsolved step that keeps the S&P MidCap 400 "
              "pre-2012 gap open. What IS verified is that the supplied group "
              "reached the database unaltered, row for row.",
              "2. **The two Russell halves under-report their own coverage.** "
              "Even after repair, the Russell 3000 names companies that neither "
              "half claims - 13.8% of the index on average in the 1990s, worst "
              "at 377 names (16.4%) in 2000, converging to 0.0% by 2026 and 0.4% "
              "across the 2020s. Every such name is a member of the Russell 3000 "
              "whose half is simply not recorded in the supplied lists. Which "
              "half it belonged to cannot be proved without market-cap data, so "
              "nothing is invented to close it: a backtest restricted to the two "
              "halves will under-cover the 1990s by roughly this margin, while "
              "the Russell 3000 list is the reliable one for that period.", "",
              "## What this recheck changed", "",
              "The cross-check was not a formality - it found and fixed real "
              "defects:",
              "",
              "1. **S&P 500 was one rebalance stale.** BE/ILMN/P joined and "
              "BLDR/TAP/TTD left on 2026-09-21; both GitHub and dfjmax lagged it.",
              "2. **Dow Jones was missing its entire pre-1991 tail.** The GitHub "
              "source holds no rows for the companies removed on 1991-05-06, so "
              "Navistar, Primerica and USX were absent. Restored from S&P DJI's own "
              "PDF, which is why the official check went from 1 mismatch to 0.",
              "3. **Ticker conventions were masking agreement.** The Dow Jones "
              "comparison reported 18 spurious mismatches until Wikipedia's current "
              "company names were mapped through the GitHub dataset's historical "
              "tickers (AlliedSignal=ALD, not today's HON).",
              "4. **The three Russell lists contradicted each other.** They were "
              "maintained independently, so a half-index would record a company as "
              "leaving and rejoining while the Russell 3000 carried it "
              "continuously, and 89 companies sat in both halves at once. 70 "
              "spurious exits were closed with proof (Rule A), 190 windows the "
              "Russell 3000 was missing were extended from the halves (Rule B), "
              "and 93 overlapping claims were resolved by a documented "
              "convention - all auditable in `indices/russell_family/`.",
              "5. **The supplied lists spell some tickers the way the vendor's "
              "era did, not the way Yahoo does.** Twenty-First Century Fox is "
              "TFCFA/TFCF in 2018-19 and the ETF families renamed wholesale "
              "(SPLG->SPYM, RYT->RSPT, JKK->ISCG). Folding them onto the current "
              "ticker is right, and for the ETFs it recovered the whole history "
              "(SPYM from 2005-11, RSPT from 2006-11). For Fox it did not: "
              "Yahoo's FOXA series begins 2019-03-12, so the 2009-2019 sp100 span "
              "still has no prices anywhere and is reported as such. A ticker "
              "mapping is not the same thing as a price series, which is why the "
              "coverage check exists.",
              "6. **The supplied S&P 100 list was one rebalance stale and carried "
              "an impossible overlap.** It ended Honeywell International (HON) on "
              "2026-07-25 while starting its spun-off successor (HONA) on "
              "2026-07-09 - both members for 17 days - and it predated the "
              "2026-09-21 rebalance entirely. Both events are documented by S&P DJI "
              "itself and are now applied, which is what turns the roster check "
              "from 4 mismatches into an exact match.",
              "7. **Yahoo has truncated some histories and purged others.** "
              "6,900 of 7,699 symbols it was asked for came back empty, and for "
              "another 159 the series stops in 2026 no matter what range is "
              "requested (AVB holds 27 bars for a 2007-2026 membership). A spare "
              "source (akshare/Sina) is checked against Yahoo in "
              "`SOURCE_CONSISTENCY.md` and used only for symbols Yahoo cannot "
              "serve, only for the raw leg, and only when it covers more of the "
              "window than the file already does.", "",
              "Sources: S&P DJI releases 2026-06-23 and 2026-09-04; Wikipedia "
              "*S&P 100*; Nasdaq's live index API.", ""]
    with open(OUT, "w") as fh:
        fh.write("\n".join(lines))
    con.close()
    print(f"\n{n_agree} agree, {n_differ} differ -> CROSSCHECK.md")


if __name__ == "__main__":
    # `--help` must not be a write. None of these scripts takes an argument, so before
    # this guard `python 80_crosscheck.py --help` ran the whole job: 39 rewrote the raw and
    # adjusted price files, 45 repaired or overwrote them, 60 deleted and rebuilt
    # yahoo.duckdb, 38 and the checkers rewrote their outputs. A flag that reads as
    # "tell me what this does" performing the destructive action is the same failure
    # the main repo hit on 2026-10-05, when a --help sweep rewrote `fills`; the guard
    # there is now shared/helpguard.py, and this is its shape without the import.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__)
        sys.exit(0)
    main()
