#!/usr/bin/env python3
"""Single source of truth for every path and constant in this workspace.

Nothing else hardcodes a path. If the tree moves, only this file changes.
"""
import datetime
import os

# Self-locating: ROOT is the workspace folder holding this scripts/ dir, so the
# tree can be moved/renamed without editing anything (this file stays the only
# place a path is defined). It lives at dte_21_screener/market_data/.
from data_providers.runtime import ROOT as DATA_HOME
ROOT = str(DATA_HOME / "market_data")

# ---- price data -----------------------------------------------------------
DATA = os.path.join(ROOT, "data")
ADJUSTED = os.path.join(DATA, "adjusted")      # split+dividend adjusted OHLCV
RAW = os.path.join(DATA, "raw")                # unadjusted twin (QC only)
QUARANTINE = os.path.join(DATA, "quarantine")
CORP_ACTIONS = os.path.join(DATA, "corporate_actions")

# ---- index membership -----------------------------------------------------
INDICES = os.path.join(ROOT, "indices")

# slug -> human label. The slug is the folder name under indices/.
INDEX_NAMES = {
    "sp100":       "S&P 100",
    "nasdaqbiotech": "Nasdaq Biotechnology Index",
    "russell1000": "Russell 1000",
    "russell2000": "Russell 2000",
    "russell3000": "Russell 3000",
    "sp500":       "S&P 500",
    "dowjones":    "Dow Jones Industrial Average",
    "nasdaq100":   "Nasdaq-100",
    "spmidcap400": "S&P MidCap 400",
    # Not an index: a custom ETF universe the user maintains for strategy S002
    # (and later research). It is built, stored, exported and checked exactly
    # like an index, because for this workspace a "group" is a membership list.
    "etf311":      "311 ETFs",
    # Also a custom group, but DERIVED rather than supplied: the 500 US-listed ETFs
    # with the most dollar volume, measured by scripts/15_fetch_etf_liquidity.py over
    # the whole market Nasdaq's screener returns (NYSE Arca and Cboe names included).
    # Its list file is generated, so re-running 15 and then 20 refreshes the membership
    # as liquidity moves -- that is the point of deriving it.
    "etf500":      "Top 500 US ETFs by dollar volume",
}
INDEX_SLUGS = list(INDEX_NAMES)

# ---- external sources -----------------------------------------------------
SOURCES = os.path.join(ROOT, "sources")
WIKI_RAW = os.path.join(SOURCES, "wikipedia")
GROUP_LISTS = os.path.join(SOURCES, "group_lists")

# The supplied AmiBroker master file names its groups with the index's
# published name; these map that name to the slug this workspace builds and to
# the frozen copy of the group in sources/independent/. 80_crosscheck uses them
# to prove the master reached the database unaltered, which is the only
# verification available for the indexes that have no second source.
XML_GROUP_SLUGS = {
    "S&P 100": "sp100",
    "Nasdaq Biotechnology": "nasdaqbiotech",
    "Russell 1000": "russell1000",
    "Russell 2000": "russell2000",
    "Russell 3000": "russell3000",
    "S&P 500": "sp500",
    "Dow Jones Industrial Average": "dowjones",
    "Nasdaq 100": "nasdaq100",
    "S&P MidCap 400": "spmidcap400",
}
XML_GROUP_LISTS = {
    "S&P 100": "sp100_user_list.txt",
    "Nasdaq Biotechnology": "nbi_user_list.txt",
    "Russell 1000": "russell1000_user_list.txt",
    "Russell 2000": "russell2000_user_list.txt",
    "Russell 3000": "russell3000_user_list.txt",
    "S&P 500": "sp500_user_list.txt",
    "Dow Jones Industrial Average": "djia_user_list.txt",
    "Nasdaq 100": "ndx_user_list.txt",
    "S&P MidCap 400": "sp400_user_list.txt",
}

# Vendor ticker vintages whose company kept trading under a different symbol.
#
# The supplied lists mark a reused ticker string with a -YYYY suffix (the year
# that string stopped being the company's ticker). Stripping the suffix is right
# for a genuinely reused symbol, but where the company simply changed ticker the
# prices are already on disk under the new symbol - so the vintage spelling
# reads as a delisted ticker with no data and silently drops real price history
# out of the dataset.
#
# Only verified pairs belong here: a wrong entry would attach one company's
# prices to another company's membership.
#
#   TFCFA / TFCF - Twenty-First Century Fox took these tickers in late 2018
#   while FOXA/FOX were being reserved for the spun-off Fox Corporation, and
#   reverted to FOXA/FOX in March 2019. Same companies throughout; FOXA and FOX
#   both have full price history on disk.
#
#   The nine below are the same thing among ETFs: the fund family rebranded and
#   Yahoo serves the whole history under the new ticker, so the old ticker looks
#   delisted. Each was resolved by searching Yahoo for the fund's own name and
#   then checking that the successor's history starts where the fund launched:
#   SPYM begins 2005-11 (SPLG launched 2005), RSP 2003-05 (RGI), ISCG/IMCG
#   2004-07 (JKK/JKH), and the five RSP* funds 2006-11 (the Rydex sector ETFs).
VINTAGE_RENAMES = {
    "TFCFA": "FOXA",
    "TFCF": "FOX",
    "SPLG": "SPYM",     # State Street SPDR Portfolio S&P 500
    "RYT": "RSPT",      # Rydex -> Invesco equal-weight technology
    "RYE": "RSPG",      # ... energy
    "RYU": "RSPU",      # ... utilities
    "RYH": "RSPH",      # ... health care
    "RHS": "RSPS",      # ... consumer staples
    "RGI": "RSP",       # Rydex S&P 500 equal weight -> Invesco RSP
    "JKK": "ISCG",      # iShares Morningstar small-cap growth
    "JKH": "IMCG",      # iShares Morningstar mid-cap growth
}

# Wikipedia page or revision used for each index's membership history, with the
# reason it was chosen. Recorded in the provenance file so the build is
# reproducible even though the live pages keep changing.
WIKI_PAGES = {
    "sp500_live": {
        "title": "List of S&P 500 companies",
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "why": "current constituents + Date added",
    },
    # The changes table was deleted from the live article between 2026-07-21 and
    # 2026-08-19, so the last revision that still carries it is pinned here.
    "sp500_changes": {
        "oldid": 1365256480,
        "title": "List of S&P 500 companies",
        "why": "last revision retaining 'Selected changes' table (removed ~2026-08)",
    },
    # The S&P 100 lives on the main article, not a "List of" page. Its
    # components table carries Symbol/Name/Sector and is the only free roster
    # of the index, which is why it is the independent recheck for sp100.
    "sp100_live": {
        "title": "S&P 100",
        "url": "https://en.wikipedia.org/wiki/S%26P_100",
        "why": "current constituents (components table)",
    },
    "spmidcap400_live": {
        "title": "List of S&P 400 companies",
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
        "why": "current constituents + full changes table",
    },
    "nasdaq100_live": {
        "title": "Nasdaq-100",
        "url": "https://en.wikipedia.org/wiki/Nasdaq-100",
        "why": "annual constituent changes table",
    },
    "dowjones_live": {
        "title": "Historical components of the Dow Jones Industrial Average",
        "url": ("https://en.wikipedia.org/wiki/"
                "Historical_components_of_the_Dow_Jones_Industrial_Average"),
        "why": "30-component snapshot per change date since 1939",
    },
}

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ---- build constants ------------------------------------------------------
DATA_START = datetime.date(1990, 1, 1)   # requested coverage floor
TODAY = datetime.date(2026, 9, 24)       # build date; 'Present' resolves here

# The REAL current date, which is NOT the build date and must not be conflated with it.
#
# TODAY is a property of the DATASET: it is the day the download was taken, and index
# membership windows resolve their 'Present' to it. That is a fixed fact about the data
# and should not drift. But two callers were using it to mean "now", and a build date is
# only "now" on the day of the build: 39_drop_inconsistent_rows.row_fault() calls any bar
# after TODAY+1day "in the future", so once the price files moved on to 2026-10-02 they
# were all classified as corrupt. It would have rewritten 10,522 settled bars out of the
# raw and adjusted files -- including the whole completed 2026-10-02 session -- and logged
# them as future-dated bad data. Bars cannot be in the future relative to the actual
# today; only relative to a frozen constant can they appear to be.
NOW = datetime.date.today()
STAMP = TODAY.strftime("%Y%m%d")
SUFFIX = "_yahoo"                        # SQX list symbol suffix

MIN_BARS = 20        # fewer bars than this is a stub, not a tradable history
TOL_DAYS = 7         # slack when matching a symbol's data against its window

# The price files store four decimals and the database treats a zero leg as
# unusable - a zero is a stub, not a price. A Yahoo price below half a tick
# (0.00004) passes a plain "> 0" test and still lands in the file as 0.0000, so
# every writer filters on the value that will be WRITTEN, not the float that
# arrived. 38_trim_zero_rows.py is the matching cleanup pass for files that were
# written before this rule existed.
MIN_PRICE = 0.00005

# index ticker -> the later ticker the same company still trades under.
# Used when the index-era symbol is dead but the company survives.
SUBSTITUTE = {
    "ERICY": "ERIC", "FI": "FISV", "IDPH": "BIIB", "KFT": "MDLZ",
    "NLOK": "GEN", "SYMC": "GEN", "PCLN": "BKNG", "TMPW": "MNST",
}
