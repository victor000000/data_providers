#!/usr/bin/env python3
"""Curated mapping: DJIA company name as written on Wikipedia -> Yahoo ticker.

The Wikipedia article lists Dow components by COMPANY NAME, and it renames
companies as they rebrand (Wal-Mart Stores -> Walmart, Minnesota Mining ->
3M, AlliedSignal -> Honeywell). One company therefore appears under several
spellings across the 1987-2026 snapshots. This map folds every spelling onto a
single ticker so the snapshots can be diffed to produce clean add/remove events.

Tickers are the ones valid on Yahoo; delisted ones (NAV, BS, Z, UK, S, WX, ALD,
DWDP, SBC, ...) are kept as-is and simply have no price file, which the dataset
builder reports rather than silently dropping.
"""

NAME_TO_TICKER = {
    # --- still in the index today -----------------------------------------
    "3M Company": "MMM",
    "Minnesota Mining & Manufacturing Company": "MMM",
    "Alphabet Inc.": "GOOGL",
    "Amazon.com, Inc.": "AMZN",
    "American Express Company": "AXP",
    "Amgen Inc.": "AMGN",
    "Apple Inc.": "AAPL",
    "Bank of America Corporation": "BAC",
    "The Boeing Company": "BA",
    "The Coca-Cola Company": "KO",
    "Caterpillar Inc.": "CAT",
    "Chevron Corporation": "CVX",
    "Cisco Systems, Inc.": "CSCO",
    "Dow Inc.": "DOW",
    "Goldman Sachs Group, Inc.": "GS",
    "The Goldman Sachs Group, Inc.": "GS",
    "The Home Depot, Inc.": "HD",
    "Honeywell International": "HON",
    "Honeywell International Inc.": "HON",
    "Honeywell Technologies Inc.": "HON",
    "Allied-Signal Incorporated": "HON",   # merged into Honeywell 1999
    "AlliedSignal Incorporated": "HON",
    "International Business Machines Corporation": "IBM",
    "Johnson & Johnson": "JNJ",
    "JPMorgan Chase & Co.": "JPM",
    "J.P. Morgan & Company": "JPM",
    "McDonald's Corporation": "MCD",
    "Merck & Co., Inc.": "MRK",
    "Microsoft Corporation": "MSFT",
    "Nike, Inc.": "NKE",
    "Nvidia Corporation": "NVDA",
    "The Procter & Gamble Company": "PG",
    "Salesforce, Inc.": "CRM",
    "The Sherwin-Williams Company": "SHW",
    "The Travelers Companies, Inc.": "TRV",
    "Travelers Inc.": "TRV",
    "UnitedHealth Group Inc.": "UNH",
    "UnitedHealth Group Incorporated": "UNH",
    "Verizon Communications Inc.": "VZ",
    "Visa Inc.": "V",
    "Walmart Inc.": "WMT",
    "Wal-Mart Stores, Inc.": "WMT",
    "The Walt Disney Company": "DIS",

    # --- former components -------------------------------------------------
    "Alcoa Inc.": "AA",
    "Aluminum Company of America": "AA",
    "Altria Group Incorporated": "MO",
    "Altria Group, Inc.": "MO",
    "Altria Group, Incorporated": "MO",
    "Philip Morris Companies Inc.": "MO",
    "American International Group, Inc.": "AIG",
    "AT&T Corporation": "T",
    "American Telephone and Telegraph Company": "T",
    "AT&T Inc.": "T",                       # SBC took the AT&T name in 2005
    "SBC Communications Inc.": "T",
    "Bethlehem Steel Corporation": "BS",
    "Citigroup Inc.": "C",
    "Primerica": "C",                       # Primerica -> Travelers -> Citigroup
    "DowDuPont Inc.": "DWDP",
    "E.I. du Pont de Nemours & Company": "DD",
    "Eastman Kodak Company": "KODK",
    "Exxon Corporation": "XOM",
    "Exxon Mobil Corporation": "XOM",
    "F. W. Woolworth Company": "Z",
    "General Electric Company": "GE",
    "General Motors Corporation": "GM",
    "Goodyear Tire and Rubber Company": "GT",
    "Hewlett-Packard Company": "HPQ",
    "Inco Limited": "N",
    "Intel Corporation": "INTC",
    "International Paper Company": "IP",
    "Kraft Foods Inc.": "KFT",
    "Navistar International Corporation": "NAV",
    "Owens-Illinois, Inc.": "OI",
    "Pfizer Inc.": "PFE",
    "Raytheon Technologies Corporation": "RTX",
    "United Technologies Corporation": "RTX",
    "Sears Roebuck & Company": "S",
    "Texaco Incorporated": "TX",
    "Union Carbide Corporation": "UK",
    "USX Corporation": "X",
    "Walgreens Boots Alliance, Inc.": "WBA",
    "Westinghouse Electric Corporation": "WX",
    "Venator": "VNTR",
}


def ticker_for(name):
    """Return the Yahoo ticker for a Wikipedia component name, or None."""
    n = " ".join(str(name).split())
    if n in NAME_TO_TICKER:
        return NAME_TO_TICKER[n]
    # tolerate punctuation/branding drift without inventing new companies
    stripped = n.replace(", Inc.", "").replace(" Inc.", "").replace(", Incorporated", "")
    stripped = stripped.replace(" Corporation", "").replace(" Company", "").replace(" Corp.", "")
    for k, v in NAME_TO_TICKER.items():
        k2 = k.replace(", Inc.", "").replace(" Inc.", "").replace(", Incorporated", "")
        k2 = k2.replace(" Corporation", "").replace(" Company", "").replace(" Corp.", "")
        if k2 == stripped:
            return v
    return None
