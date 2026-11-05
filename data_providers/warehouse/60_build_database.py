#!/usr/bin/env python3
"""Build yahoo.duckdb - the master store for the entire workspace.

Everything lives here: prices, corporate actions, index membership, delisting
status and quarantine. The CSVs and the stock-group .txt lists are EXPORTS
produced from this database by scripts/70_export.py, not separate sources of
truth.

Schema
------
prices(symbol, date, open, high, low, close, volume,
       adj_open, adj_high, adj_low, adj_close)
    ONE row per symbol-date holding the raw bar and the adjusted bar side by
    side. Storing them together (rather than as two tables, or as a table plus
    a derived factor) means an export reproduces the original files exactly,
    with no rounding drift from re-deriving adjusted = raw * factor.

symbols          one row per ticker: first/last date, bar count, whether it is
                 still trading, and its delisting date when it is not.
dividends        ex-date + amount per symbol.
splits           date + ratio per symbol.
indices          the four index definitions.
index_membership point-in-time membership: symbol, index, start, end, status.
index_excluded   index members with no usable price data, and why.
quarantine       symbols parked out of the build, and why.

Rebuilding is cheap and safe: delete the file and re-run. It reads the CSVs
already on disk and never touches the network.
"""
import os
import sys
import time

import duckdb

from data_providers.warehouse import config as C

DB = os.path.join(C.ROOT, "yahoo.duckdb")


def q(con, sql, label=None):
    t0 = time.time()
    con.execute(sql)
    if label:
        print(f"  {label:44} {time.time()-t0:6.1f}s", flush=True)


def main():
    import uuid
    # A rebuild must leave the previous store readable until its replacement closes.
    pending = DB + '.building-' + uuid.uuid4().hex
    con = duckdb.connect(pending)
    con.execute("SET memory_limit='6GB'")
    con.execute(f"SET temp_directory='{os.path.join(C.ROOT, '.duckdb_tmp')}'")
    con.execute("SET preserve_insertion_order=false")

    print("1. prices")
    cols = ("{'column0':'VARCHAR','column1':'DOUBLE','column2':'DOUBLE',"
            "'column3':'DOUBLE','column4':'DOUBLE','column5':'BIGINT'}")

    def scan(folder):
        # DISTINCT: a CSV may carry the same bar more than once (a repair or a
        # tail-append can rewrite a line that is already there). The duplicate
        # lines are identical, so collapsing them is lossless - and it keeps
        # prices at exactly one row per symbol-date by construction rather than
        # by trusting every writer to be idempotent.
        return f"""
        SELECT DISTINCT
               regexp_extract(filename, '([^/\\\\]+)\\.csv$', 1) AS symbol,
               strptime(column0, '%d%m%Y')::DATE AS date,
               column1 AS open, column2 AS high, column3 AS low,
               column4 AS close, column5 AS volume
        FROM read_csv('{folder}/*.csv', header=false, filename=true,
                      columns={cols}, ignore_errors=true)
        WHERE regexp_matches(column0, '^[0-9]{{8}}$')
          -- Drop placeholder bars: Yahoo emits all-zero OHLC rows for delisted
          -- or halted stubs. They are not prices and must not enter the data.
          AND column1 > 0 AND column2 > 0 AND column3 > 0 AND column4 > 0
        """

    q(con, f"CREATE TEMP TABLE r AS {scan(C.RAW)}", "scan raw")
    q(con, f"CREATE TEMP TABLE a AS {scan(C.ADJUSTED)}", "scan adjusted")
    q(con, """
      CREATE TABLE prices AS
      SELECT r.symbol, r.date,
             r.open, r.high, r.low, r.close, r.volume,
             a.open AS adj_open, a.high AS adj_high,
             a.low  AS adj_low,  a.close AS adj_close
      FROM r LEFT JOIN a USING (symbol, date)
      ORDER BY r.symbol, r.date
    """, "join -> prices (raw + adjusted per row)")
    con.execute("DROP TABLE r")
    con.execute("DROP TABLE a")

    print("2. catalog")
    q(con, """
      CREATE TABLE symbols AS
      WITH agg AS (
        SELECT symbol, min(date) AS first_date, max(date) AS last_date,
               count(*) AS bars,
               count(adj_close) AS adj_bars
        FROM prices GROUP BY symbol),
      span AS (SELECT max(last_date) AS market_last FROM agg)
      -- The window is a QUARTER, not ten days, and delist_date says it is inferred.
      --
      -- A stale tail is not a delisting. The bars stop where the last fetch reached,
      -- and 35_refresh_tail.py exists precisely because those tails lag -- so at ten
      -- days, "we have not downloaded this recently" was being written into the
      -- database as "this stopped trading". 39 symbols sat 11-30 days behind, ZBH
      -- among them at 11 days: a current member of three indexes, asserted delisted.
      -- The inference is only credible at a distance where a fetch lag stops being an
      -- explanation, and on this data that is unambiguous: 2,061 of the 2,174
      -- inactive symbols are more than 90 days behind, so a quarter keeps every real
      -- delisting and drops every false one in the marginal band.
      --
      -- delist_basis carries the judgement, because `delist_date` alone reads as an
      -- observed fact and this is a heuristic over our own download coverage.
      SELECT a.symbol, a.first_date, a.last_date, a.bars, a.adj_bars,
             (a.last_date >= (SELECT market_last FROM span) - INTERVAL 90 DAY)
                 AS is_active,
             CASE WHEN a.last_date < (SELECT market_last FROM span) - INTERVAL 90 DAY
                  THEN a.last_date END AS delist_date,
             CASE WHEN a.last_date < (SELECT market_last FROM span) - INTERVAL 90 DAY
                  THEN 'inferred: no bars for a quarter (not observed)' END AS delist_basis
      FROM agg a ORDER BY a.symbol
    """, "symbols (+ delisting)")

    # Bounded to the span the symbol actually has prices for. An action outside
    # it cannot affect a single stored bar - there is no price on that side of
    # the series to restate - so keeping it only makes the table contradict
    # `symbols`: 13 NDSN dividends sat in 1986-89 against a file that starts
    # 1990-01-02, and AVB carried a split dated after its last bar. The fetch
    # windows for prices and actions are set independently upstream, so this is
    # where they are reconciled.
    q(con, f"""
      CREATE TABLE dividends AS
      SELECT d.symbol, CAST(d.date AS DATE) AS date, CAST(d.amount AS DOUBLE) AS amount
      FROM read_csv('{C.CORP_ACTIONS}/dividends.csv', header=true) d
      JOIN symbols s ON s.symbol = d.symbol
      WHERE CAST(d.date AS DATE) BETWEEN s.first_date AND s.last_date
    """, "dividends (bounded to each symbol's price span)")
    q(con, f"""
      CREATE TABLE splits AS
      SELECT x.symbol, CAST(x.date AS DATE) AS date, CAST(x.ratio AS DOUBLE) AS ratio
      FROM read_csv('{C.CORP_ACTIONS}/splits.csv', header=true) x
      JOIN symbols s ON s.symbol = x.symbol
      WHERE CAST(x.date AS DATE) BETWEEN s.first_date AND s.last_date
    """, "splits (bounded to each symbol's price span)")

    print("3. index membership")
    q(con, "CREATE TABLE indices(slug VARCHAR PRIMARY KEY, name VARCHAR)")
    con.executemany("INSERT INTO indices VALUES (?,?)",
                    [(s, C.INDEX_NAMES[s]) for s in C.INDEX_SLUGS])

    mem = []
    for slug in C.INDEX_SLUGS:
        d = os.path.join(C.INDICES, slug)
        mem.append(f"""
          SELECT '{slug}' AS index_slug, symbol, company,
                 NULLIF(start_date,'')::DATE AS start_date,
                 NULLIF(end_date,'')::DATE   AS end_date,
                 status, source, note
          FROM read_csv('{d}/membership.csv', header=true, all_varchar=true)""")
    q(con, "CREATE TABLE index_membership AS " + " UNION ALL ".join(mem),
      "index_membership")

    print("4. quarantine")
    # each quarantined symbol can have both an adjusted and a raw file, so
    # collect into a set - one row per (symbol, bucket), not per file
    seen = set()
    for bucket in ("delisted_no_data", "single_bar_unsettled"):
        p = os.path.join(C.QUARANTINE, bucket)
        if not os.path.isdir(p):
            continue
        for f in os.listdir(p):
            sym = (f[:-4].replace("raw__", "")
                   .replace("yahoo_to_sqx_20260923__", ""))
            seen.add((sym, bucket))
    rows = sorted(seen)
    con.execute("CREATE TABLE quarantine(symbol VARCHAR, bucket VARCHAR)")
    if rows:
        con.executemany("INSERT INTO quarantine VALUES (?,?)", rows)
    print(f"  {'quarantine':44} {len(rows)} rows")

    print("5. views")
    q(con, """
      CREATE VIEW prices_raw AS
      SELECT symbol, date, open, high, low, close, volume FROM prices
    """, "view prices_raw")
    q(con, """
      CREATE VIEW prices_adjusted AS
      SELECT symbol, date, adj_open AS open, adj_high AS high,
             adj_low AS low, adj_close AS close, volume,
             adj_close / NULLIF(close,0) AS adj_factor
      FROM prices
    """, "view prices_adjusted")
    # THE usability rule. Defined once, here, so the exported lists and the
    # audit CSVs can never disagree about which symbols are in a dataset.
    q(con, f"""
      CREATE VIEW membership_check AS
      SELECT m.index_slug, m.symbol, m.company, m.status,
             m.start_date AS index_start, m.end_date AS index_end,
             s.first_date AS data_start, s.last_date AS data_end,
             s.bars, s.is_active,
             CASE
               WHEN s.symbol IS NULL THEN 'no price data on Yahoo (delisted)'
               WHEN s.bars < {C.MIN_BARS} THEN 'stub: only ' || s.bars || ' bars'
               WHEN NOT (s.first_date <= COALESCE(m.end_date, DATE '{C.TODAY}')
                                           + INTERVAL {C.TOL_DAYS} DAY
                     AND s.last_date  >= COALESCE(m.start_date, DATE '{C.DATA_START}')
                                           - INTERVAL {C.TOL_DAYS} DAY)
                 THEN 'data ' || s.first_date || '..' || s.last_date
                      || ' does not overlap the index window'
             END AS unusable_reason
      FROM index_membership m LEFT JOIN symbols s ON s.symbol = m.symbol
    """, "view membership_check (the usability rule)")

    q(con, """
      CREATE VIEW dataset AS
      SELECT index_slug, symbol,
             min(company) AS company, min(status) AS status,
             min(index_start) AS index_start, max(index_end) AS index_end,
             min(data_start) AS data_start, max(data_end) AS data_end,
             max(bars) AS bars, bool_or(is_active) AS is_active
      FROM membership_check WHERE unusable_reason IS NULL
      GROUP BY index_slug, symbol
    """, "view dataset (usable members only)")
    for slug in C.INDEX_SLUGS:
        q(con, f"""CREATE VIEW dataset_{slug} AS
                   SELECT * FROM dataset WHERE index_slug='{slug}' ORDER BY symbol""",
          f"view dataset_{slug}")

    q(con, """
      CREATE TABLE index_excluded AS
      SELECT index_slug, symbol, min(unusable_reason) AS reason
      FROM membership_check
      GROUP BY index_slug, symbol
      HAVING count(*) FILTER (WHERE unusable_reason IS NULL) = 0
      ORDER BY index_slug, symbol
    """, "index_excluded (derived from the same rule)")
    q(con, """
      CREATE VIEW adjustment_summary AS
      SELECT p.symbol, count(*) AS bars,
             sum(CASE WHEN p.adj_close <> p.close THEN 1 ELSE 0 END) AS bars_adjusted,
             (SELECT count(*) FROM dividends d WHERE d.symbol=p.symbol) AS dividends,
             (SELECT count(*) FROM splits    s WHERE s.symbol=p.symbol) AS splits
      FROM prices p GROUP BY p.symbol
    """, "view adjustment_summary")

    print("\ndatabase summary")
    for t in ["prices", "symbols", "dividends", "splits", "indices",
              "index_membership", "index_excluded", "quarantine"]:
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"  {t:20} {n:>12,}")
    act = con.execute("SELECT count(*) FROM symbols WHERE is_active").fetchone()[0]
    print(f"  {'  (of which active)':20} {act:>12,}")
    con.close()
    with open(pending, 'rb') as durable:
        os.fsync(durable.fileno())
    os.replace(pending, DB)
    print(f"\n  db size {os.path.getsize(DB)/1e6:.0f} MB -> {DB}")


if __name__ == "__main__":
    # `--help` must not be a write. None of these scripts takes an argument, so before
    # this guard `python 60_build_database.py --help` ran the whole job: 39 rewrote the raw and
    # adjusted price files, 45 repaired or overwrote them, 60 deleted and rebuilt
    # yahoo.duckdb, 38 and the checkers rewrote their outputs. A flag that reads as
    # "tell me what this does" performing the destructive action is the same failure
    # the main repo hit on 2026-10-05, when a --help sweep rewrote `fills`; the guard
    # there is now shared/helpguard.py, and this is its shape without the import.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__)
        sys.exit(0)
    main()
