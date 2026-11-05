import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

# ── --help guard ─────────────────────────────────────────────────────────────
# This script WRITES to a database and takes no arguments, so `script.py --help`
# used to execute it in full. Not hypothetical: a 2026-10-05 sweep ran every app
# with --help and rewrote the fills table (via seed_fills.py) and risk_profile.
# A read-only-looking flag must not be a write. Exits before any connection.
from data_providers.shared import helpguard
helpguard.handle_help(__file__)   # reads the module summary from the SOURCE:
# __doc__ is None here -- the description sits after the bootstrap below, and a
# docstring must be the first statement, so all 33 copies printed "(no docstring)".

"""build_symbol_map.py — one row per (canonical instrument, data source, that source's spelling).

Why this exists: the same instrument is spelled differently by every vendor, and today almost
every serious bug traced back to a spelling mismatch nobody had written down.

    Instrument      Yahoo      Alpaca/OCC   IBKR              akshare / other
    ---------------------------------------------------------------------------
    Berkshire B     BRK-B      BRKB         BRK B             -
    Crawford A      CRD-A      CRDA         CRD A             -
    Carlisle        CRD-B      CRDB         CRD B             -
    VIX future      ^VIX?      -            VIX @ CFE         -      ("VX" = no such thing)
    Methanol        -          -            -                 CZCE 'MA' (China)
    Mastercard      MA         MA           MA                -      (collides with CZCE MA)
    Crude option    -          -            -                 INE '原油期权' (Chinese name)

Getting this wrong is not a cosmetic error, it is one of:
  * a silently empty fetch (BRK-B to Alpaca/OCC -> HTTP 400 invalid symbol; the whole batch lost)
  * a WRONG instrument fetched under a right-looking ticker (CZCE 'MA' methanol vs NYSE 'MA'
    Mastercard — both real, both plausible, only one is what you meant)
  * a duplicate download (the same future pulled twice under two spellings)

So the map is not documentation, it is the lookup every downloader should consult before
constructing a symbol. `canonical` is the key we store in our own tables; every other column
is how a specific source wants it spelled.

    uv run --no-project --with duckdb python scripts/build_symbol_map.py
"""
import pathlib
import sys

from data_providers.data import db  # noqa: E402

ROOT = bootstrap.ROOT

DDL = """
CREATE TABLE IF NOT EXISTS ref.symbol_map (
    canonical   TEXT,        -- our key: what we store in instruments/universe tables
    source      TEXT,        -- yahoo | alpaca | ibkr | futu | akshare | schwab
    symbol      TEXT,        -- how THAT source spells it
    kind        TEXT,        -- equity | etf | index | future | option | cn_future
    note        TEXT,
    -- (canonical, source, symbol), not (canonical, source): a single source can legitimately
    -- spell one instrument two ways. INE is the live case — akshare takes BOTH the code 'SC'
    -- and the Chinese name '原油期权' for the same product, and the narrower key silently
    -- dropped one of them.
    PRIMARY KEY (canonical, source, symbol)
)"""


def rows_from_universes():
    """Derive what we can mechanically: class shares, futures, CN products."""
    out = []

    # CANONICAL = THE IBKR SYMBOL (user, 2026-10-01). IBKR covers equities, ETFs, indices,
    # futures and options under one convention, so making it the base means only FOREIGN
    # sources need translating — instead of every source needing to agree with every other.
    # Yahoo's '-' becomes IBKR's space (BRK B); OCC roots then strip to BRKB.
    for yh in [l.strip() for l in (ROOT / "plans" / "universe_download.txt").read_text().splitlines()
               if l.strip() and not l.startswith("#")]:
        ibkr = yh.replace("-", " ") if ("-" in yh and yh.split("-", 1)[1][:1].isalpha()) else yh
        canon = ibkr                      # <-- IBKR spelling IS the key
        if ibkr != yh:
            out += [(canon, "ibkr", ibkr, "equity", "IBKR class-share spelling (SPACE)"),
                    (canon, "yahoo", yh, "equity", "Yahoo class-share spelling (hyphen)"),
                    (canon, "alpaca", yh.replace("-", ""), "equity", "OCC root: no hyphen")]
        else:
            # alpaca is emitted for EVERY equity, not just class shares. A plain ticker
            # happens to match the IBKR spelling, but "happens to match" is not an entry —
            # a source with no row is a source nobody can check, and the map's job is to be
            # the thing you look in, not the thing you assume.
            out += [(canon, "ibkr", ibkr, "equity", "key = IBKR symbol"),
                    (canon, "yahoo", yh, "equity", ""),
                    (canon, "alpaca", yh.replace("-", "").replace(".", "").upper(), "equity",
                        "OCC root: no hyphen or dot")]
        # Futu namespaces by market: US.<sym>, SH.<code>, HK.<code>. Adding every source to
        # the map is what makes it a MAP rather than a two-vendor lookup — a symbol that
        # reaches a vendor without an entry is the failure mode this table exists to stop.
        out.append((canon, "futu", f"US.{ibkr.replace(' ', '.')}", "equity",
                    "Futu market-prefixed; dotted class-share suffix"))
        out.append((canon, "thetadata_stock", ibkr.replace(" ","."), "equity",
                    "Theta stock catalog; dotted class-share suffix"))
        out.append((canon, "massive", ibkr.replace(" ", "."), "equity",
                    "Massive stock ticker; dotted class-share suffix, not an OCC root"))

    # US futures + indices, straight from the TWS universe file.
    for line in (ROOT / "plans" / "universe_futures.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("FUT:"):
            _, sym, exch = line.split(":")
            out.append((f"{sym}:{exch}", "ibkr", line, "future",
                        "key = IBKR symbol:exchange (ContFuture)"))
            out.append((f"{sym}:{exch}", "futu", f"US.{sym}", "future",
                        "Futu futures use the bare root under US."))
        elif line.startswith("IDX:"):
            _, sym, exchs = line.split(":")
            out.append((f"{sym}:IDX", "ibkr", line, "index",
                        "key = IBKR symbol (Index); exchange is a fallback list"))
            out.append((f"{sym}:IDX", "futu", f"US.{sym}", "index",
                        "Futu index code."))

    # VIX future: the spelling that cost five failed guesses.
    out.append(("VIX:CFE", "ibkr", "FUT:VIX:CFE", "future",
                "IBKR symbol is VIX, NOT VX. VXV6 verified 2026-10-01. No 30d IV series served."))

    # CN futures — the namespace collision that silently returned Mastercard for methanol.
    # CN: the akshare spelling is DERIVED from cn_product_map rather than re-listed here,
    # so the two cannot drift apart. cn_schema.py is the single place a venue's convention
    # is declared (endpoint, code format, price columns).
    try:
        import duckdb as _d
        _con = _d.connect(str(ROOT / "cn_market.duckdb"), read_only=True)
        for canon, venue, prod, ak_sym, _ep, fmt, _cols in _con.execute(
                "SELECT canonical, venue, product, ak_symbol, endpoint, code_format, "
                "price_cols FROM cn_product_map").fetchall():
            out.append((canon, "akshare", ak_sym, "cn_future",
                        f"{venue} {prod}; code format {fmt}"))
            # SINA'S SPELLING IS NOT THE EXCHANGE'S, and for CZCE it is not even the same
            # length: the exchange writes SR611 (YMM) while Sina wants SR2611 (YYMM) and
            # returns an EMPTY FRAME for the exchange's form -- which reads as "no data", the
            # exact failure mode this map exists to stop. Derived here so the next consumer
            # does not re-probe it (verified 2026-10-07: SR611 -> 0 rows, SR2611 -> 214).
            out.append((canon, "sina", f"{prod}0", "cn_future",
                        f"{venue} {prod}: Sina continuous front month (M0-style)"))
            out.append((canon, "sina", f"{prod}<YYMM>", "cn_future",
                        f"{venue} {prod}: Sina wants YYMM (SR2611); the exchange's YMM "
                        f"(SR611) returns an empty frame"))
        _con.close()
    except Exception as e:
        # NOT a warning to shrug past. Every CN row under this heading — 23 products, and
        # the Sina spellings with them — lives ONLY in cn_product_map, and the run would
        # carry on to write a CSV that silently swaps a complete map for one missing all of
        # them. A truncated map is indistinguishable from a complete one to its next reader,
        # which is the exact failure this table exists to stop. Measured 2026-10-07: DuckDB
        # refuses even a READ-ONLY open while a writer holds the file, so the pipeline
        # running behind a downloader is the normal case, not an edge one.
        print(f"cn_product_map unreadable ({repr(e)[:60]}) — refusing to write a map that "
              f"would be missing every CN row. Re-run when no writer holds cn_market.duckdb.")
        raise SystemExit(3)

    for line in (ROOT / "plans" / "universe_futures_cn.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        _, exch, prod = line.split(":")
        out.append((f"CN:{exch}:{prod}", "akshare", prod, "cn_future",
                    f"{exch} product code — NEVER resolve under a US market code"))
    out.append(("CN:INE:SC", "akshare", "原油期权", "cn_future",
                "INE quotes the option by Chinese NAME, not the SC code"))
    return out


def main():
    rows = rows_from_universes()
    # Retain evidence-backed acquisition aliases beyond the original watchlist.
    # Historical metadata does not authorize excluded-market acquisitions.
    import csv
    existing = ROOT / "plans" / "symbol_map.csv"
    if existing.exists():
        with existing.open(newline="") as stream:
            rows.extend(tuple(row[k] for k in ("canonical", "source", "symbol", "kind", "note"))
                        for row in csv.DictReader(stream))

    # The map is written to CSV FIRST and loaded into reference.duckdb second, because
    # DuckDB refuses a read-write open while any downloader holds the file read-only — and
    # downloaders run for hours. A reference table that can only be built in a quiet window
    # is a reference table that never gets built; the CSV is always available, is diffable,
    # and loads in seconds when the window opens.
    # Deduplicated: universe_futures_cn.txt and cn_product_map describe the same CN products,
    # so each is derived twice and the CSV carried both — reporting 49 rows where the table,
    # keyed on (canonical, source, symbol), can only ever hold 27. The CSV is meant to be the
    # table in a diffable form, so it should not be able to say a number the table cannot.
    uniq = sorted({r[:3]: r for r in rows if r[2]}.values())
    import csv
    out = ROOT / "plans" / "symbol_map.csv"
    with out.open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["canonical", "source", "symbol", "kind", "note"])
        w.writerows(uniq)
    print(f"wrote {out} ({len(uniq)} rows)")

    from data_providers.jobs.data_ops.symbol_map_report import report_db, report_rows
    try:
        con = db.connect(log=None, reference="rw")
    except Exception as e:
        print(f"reference.duckdb busy ({repr(e)[:50]}) — CSV written, table NOT loaded.")
        print("Re-run when no downloader is active to load it.")
        report_rows(rows)
        return
    con.execute(DDL)
    for r in uniq:
        con.execute("INSERT OR REPLACE INTO ref.symbol_map VALUES (?,?,?,?,?)", r)
    # report_db, not report_rows: this branch HAS a table, so the report should read the
    # table. Printing the derived rows here as well duplicated every heading, and the copy
    # that survives is the one that can catch an insert which silently wrote nothing.
    report_db(con)
    print(f"symbol_map: {len(uniq)} rows loaded")
    con.close()


if __name__ == "__main__":
    main()
