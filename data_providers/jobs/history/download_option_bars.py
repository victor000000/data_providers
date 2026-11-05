import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""download_option_bars.py — Alpaca historical OPTION bars -> market_history.option_bars (idempotent).

For each ticker, for every monthly (3rd-Friday) expiry in range:
  anchor date = expiry - 45d; anchor price = underlying close that day;
  target strikes at anchors x (0.82, 0.88, 0.94, 0.97, 1.00, 1.03, 1.06, 1.12, 1.18),
  both Call and Put (symbols constructed in OCC format: ROOT+YYMMDD+C|P+STRIKE*1000 8d).
  One batch bars request per expiry. Invalid symbols return empty (verified).

Idempotency (no re-download): an expiry is SKIPPED if option_bars already has rows for
(source, underlying, expiry); individual rows dedup via PRIMARY KEY (source, symbol, day)
with ON CONFLICT DO NOTHING. Underlying daily bars stored once per (symbol, source) with
same-day DELETE+INSERT.

Usage:
  uv run python download_option_bars.py \
      [--tickers META CRWD ... | --tickers-file plans/data_universe_tier1.txt] \
      [--start 2024-02-01] [--end 2026-09-30]
"""
import argparse
import datetime as dt
from data_providers.data import clock
import json
import pathlib
import sys
import time

from data_providers.data import db  # scripts/db.py — knows the three database paths
from data_providers.data import symbols  # the one place vendor spellings are written down
from data_providers.shared import files  # safe file operations
from concurrent.futures import ThreadPoolExecutor
from data_providers.shared.net import status
from data_providers.providers.alpaca_http import get_json, utc_day_bounds
from data_providers.jobs.history.option_root_history import windows, contract

ROOT = bootstrap.ROOT
from data_providers.credentials import AlpacaHeaders
HDR = AlpacaHeaders()
SRC = "alpaca"
ANCHORS = (0.82, 0.88, 0.94, 0.97, 1.00, 1.03, 1.06, 1.12, 1.18)
# The screen trades ±0.16–0.22Δ legs, and 9 anchors are too coarse to land there on every
# name (ILMN came out at 0.5Δ). A dense grid — 1% steps across ±20% — costs ~4 requests
# per expiry instead of 1 and puts a strike within a delta point of the target.
# ±30%, not ±20%: at 55–60% IV with ~50 DTE a 0.19Δ strike sits ~25% away, so a ±20% band
# clips exactly the legs the screen wants (SWKS came out at 0.38Δ, CRWD's call at 0.33Δ).
ANCHORS_DENSE = tuple(round(0.70 + 0.01 * i, 2) for i in range(61))          # +-30%, 1% — targeted runs
ANCHORS_BULK = tuple(round(0.80 + 0.02 * i, 2) for i in range(21))

# Alpaca accepts 300+ symbols on one options-bars call (measured 2026-10-02, HTTP 200,
# no pagination). 100 keeps each response modest while cutting request count ~4x against
# the old hard-coded 24 -- and requests, not symbols, are what the 200/min quota counts.
SYMBOLS_PER_REQUEST = 100

# The expiry loop used to be serial -- one expiry's ~2 batches at a time through a 6-worker
# pool, so at most 2 requests were ever in flight and the rest of the pool idled. Measured
# 2026-10-02: the Alpaca sweep ran at 12.3 req/min against a 200/min allowance, i.e. 6% of
# quota. Borrowing a few hundred ms of latency per expiry does not matter; doing it 37 times
# in sequence does. The work is now flattened so every expiry's requests share one pool.
ALPACA_WORKERS = 4

# `--workers` overrides this per process. Each shard runs its OWN pool, so the box-wide
# concurrency is workers x shards. 4, not 16: the Alpaca gate caps the REQUEST RATE, not the
# concurrency — ~190/min is ~6 requests in flight at ~2 s each — so a larger pool adds no
# throughput, only concurrency, and with it DuckDB contention. Measured 2026-10-03: four
# shards at 16 workers each ran their own 16-thread query pool against the same disk and the
# device sat at 100% while real throughput was a few hundred kB/s.

# ANCHORS_BULK: +-20%, 2% steps.
# why: at 50% IV a 0.20-delta strike sits ~18-20% OTM, so +-20% covers the legs the screen
# picks. The 61-anchor grid spends 3x the requests (6 batches/expiry vs 2) on strikes no
# strategy touches — measured 2026-10-01 while watching the top-1000 job crawl.


def get(url):
    return get_json(url, HDR)


def third_fridays(start, end):
    d = start.replace(day=1)
    out = []
    while d <= end:
        # third friday of month d
        first = d
        fridays = [first + dt.timedelta(days=i) for i in range(31)
                   if (first + dt.timedelta(days=i)).month == d.month
                   and (first + dt.timedelta(days=i)).weekday() == 4]
        if len(fridays) >= 3:
            f = fridays[2]
            if start <= f <= end:
                out.append(f)
        nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        d = nxt
    return out


def round_strike(px):
    if px >= 100:
        return float(round(px))
    if px >= 20:
        return round(px * 2) / 2
    return round(px * 2) / 2


def alpaca_sym(ticker):
    """Our canonical ticker -> the spelling Alpaca's REST endpoints accept.

    Three vendors, three spellings of the same instrument, and only one works per vendor:

        ours / yahoo / OCC-stripped   BRK-B  /  BRKB
        IBKR                          BRK B      (space)
        Alpaca stocks endpoint        BRK.B      (dot)

    Verified 2026-10-02: Alpaca returns RuntimeError on /v2/stocks/BRK-B/bars. The
    failure is silent in effect -- the name simply yields no bars and looks like a
    ticker with no data rather than a spelling the API rejects.

    Only the class-share separator is translated. OCC symbols strip it entirely (occ()
    below), so this applies to the stock/underlying lookups, not the option roots.
    """
    return ticker.replace("-", ".")


OPTION_BAR_COLS = ("source", "symbol", "underlying", "expiry", "strike", "call_put", "day",
                   "open", "high", "low", "close", "volume", "vwap")


def write_bars(con, rows, chunk=500, table="mh.option_bars"):
    """Write option bars set-based, not one INSERT per row.

    Measured on this box: a single-row con.execute() costs ~12 ms, because DuckDB pays a
    whole transaction, parse, plan and primary-key index update PER ROW. That is the entire
    throughput problem in this downloader — AAPL returned 3,718 bars for one expiry, which is
    ~45 s of INSERT alone, and AAOI's 23,272 bars across a ticker is ~5 minutes of it. The
    fetch is not slow; the writing is.

    Alternatives measured against the same data: executemany no better (still one txn per
    row), an explicit BEGIN/COMMIT around per-row inserts 2.3x, and this set-based
    INSERT ... SELECT FROM (VALUES ...) 10x. Chunked at 500 rows (13 cols x 500 = 6,500
    placeholders): one statement for a whole ticker would be ~300k placeholders and hit the
    parser's limit.
    """
    ncol = len(OPTION_BAR_COLS)
    cols = ",".join(OPTION_BAR_COLS)
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        values = ",".join(["(" + ",".join(["?"] * ncol) + ")"] * len(part))
        con.execute(
            f"INSERT INTO {table} ({cols}) "
            f"SELECT * FROM (VALUES {values}) AS v({cols}) ON CONFLICT DO NOTHING",
            [x for r in part for x in r])


def occ(underlying, expiry, cp, strike):
    # Static compatibility formatter; historical requests use date-aware contract().
    try:
        canon = symbols.canonical_for("yahoo", underlying)
        if canon:
            return (f"{symbols.for_source(canon, 'alpaca')}"
                    f"{expiry.strftime('%y%m%d')}{cp}{int(round(strike * 1000)):08d}")
    except Exception:
        pass
    root = underlying.replace("-", "").replace(".", "").strip().upper()
    return f"{root}{expiry.strftime('%y%m%d')}{cp}{int(round(strike * 1000)):08d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--tickers-file", default=None)
    ap.add_argument("--start", default="2024-02-01")
    ap.add_argument("--bulk", action="store_true",
                    help="+-20% at 2%% steps (2 batches/expiry) — for 1000-name runs")
    ap.add_argument("--dense", action="store_true",
                    help="41 anchors at 1%% steps — for expiries the screen will actually trade")
    # Each concurrent shard MUST have its own staging file: DuckDB allows one writer per
    # file, so two shards sharing alpaca_bars would not run in parallel -- the second would
    # block or fail. With separate keys they write independently and merge_staging folds
    # them all in. The CPU budget is the reason this is worth doing at all: the sweep is
    # compute-bound during its skip-heavy pass (measured 4.5% of the request quota), and
    # this box has 16 cores running at load 4.
    ap.add_argument("--staging", default="alpaca_bars",
                    help="staging file key; concurrent shards need distinct values")
    ap.add_argument("--force", action="store_true",
                    help="ignore the skip lists: re-request expiries and gap-fill new strikes "
                         "(ON CONFLICT keeps what is already stored, so nothing duplicates)")
    # --force is too blunt for the common case. An expiry we already hold is normally skipped
    # outright, so widening the band (--bulk's +-20% -> --dense's +-30%) fetches NOTHING for
    # any expiry already stored -- the new outer strikes are never requested. --force fixes
    # that by re-requesting everything, including expiries that are already complete, which is
    # the duplication download_guard.py exists to catch.
    #
    # --gapfill asks the narrower question: of the strikes this band asks for, which do we NOT
    # already hold? An expiry is re-requested only when coverage falls below the threshold.
    # Measured on 2026-10-03 against the 21-60 DTE window: 215 of ~405 names could not build
    # the screen's 0.16-0.22d legs at all, and every one of them was an expiry the plain skip
    # list would have refused to revisit.
    ap.add_argument("--gapfill", action="store_true",
                    help="re-request only LIVE expiries whose stored strikes do not yet cover "
                         "the requested band (implies re-checking expiries in the skip list)")
    ap.add_argument("--gapfill-all", action="store_true",
                    help="with --gapfill, also re-check dead expiries — history recovery only")
    ap.add_argument("--workers", type=int, default=ALPACA_WORKERS,
                    help=f"requests in flight per process (default {ALPACA_WORKERS}); the "
                         "box-wide figure is this x shard count")
    ap.add_argument("--gapfill-min", type=float, default=0.90,
                    help="coverage below which a stored expiry counts as short (default 0.90)")
    # The screen trades UPCOMING expiries, so the default range reaches forward: a future
    # expiry's anchor (expiry - 45d) may not have arrived yet, in which case the anchor
    # price falls back to the latest close — refreshed automatically once that date passes.
    ap.add_argument("--end", default=(clock.today() + dt.timedelta(days=180)).isoformat())
    args = ap.parse_args()
    tickers = args.tickers or []
    if args.tickers_file:
        try:
            tickers += files.safe_read_ticker_file(args.tickers_file)
        except (FileNotFoundError, ValueError, OSError) as e:
            print(f"Error: {e}")
            return
    if not tickers:
        print("no tickers"); return
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    try:
        # A NON-DEFAULT --staging key means a concurrent shard, and a shard must never take
        # market_history read-write. If it does, it blocks the read-only snapshot every other
        # shard needs, they degrade to empty `mhro` views, and each re-fetches the whole
        # universe. Measured 2026-10-02: three shards launched, all three printed the
        # snapshot WARNING within seconds, stopped with 0 rows wasted only because the
        # warning was made loud earlier the same day. Raising here routes a shard straight
        # to the staging branch below, where readers do not block readers.
        if args.staging != "alpaca_bars":
            raise RuntimeError("shard mode: staging only, market_history left free")
        con = db.connect(log=None, history="rw", universe=True)
        yh = True
    except Exception as e:
        # Snapshot ONLY what this shard asks about: the two tables its skip/coverage logic
        # reads, projected to the columns it uses, filtered to its own tickers. The generic
        # snapshot copied 11 tables / 22.35M rows (~1.2GB) into EVERY shard's staging file at
        # launch — four shards doing that together put the disk at 100% read for minutes, for
        # data ~99% of which (by ticker) this shard never queries.
        con = db.connect(log=None, staging=args.staging, universe=True, snapshot=True,
                         snapshot_scope=tickers,
                         snapshot_tables=("option_bars", "option_bars_checks"),
                         snapshot_columns={"option_bars": ("source", "underlying", "expiry", "symbol"),
                                           "option_bars_checks": ("source", "underlying", "expiry", "bars")})   # own file -> runs concurrently with other sources
        print(f"(market_history busy, staging instead: {repr(e)[:60]})")
        # Do NOT conclude the warehouse is gone. `history="rw"` failing means MARKET_HISTORY
        # is locked (another job holds its mhro handle) — the warehouse is a different file
        # and is attached either way. Treating the two as one cost us the reuse this whole
        # path exists for: 2020-> prices re-fetched from Alpaca that the warehouse already
        # holds, AND the prune that stops them landing in underlying_daily was skipped. Ask.
        try:
            con.execute("SELECT 1 FROM yh.prices LIMIT 1")
            yh = True
        except Exception:
            yh = False
            print("(warehouse genuinely unavailable — full downloads)")
    # Reuse first: the warehouse already holds US daily history (1980->). We read anchor
    # prices from it and download only the tail it does not have; symbols outside its
    # universe still get the full Alpaca series.

    for idx, tk in enumerate(tickers):
        ymax = None
        px = {}
        if yh:
            row = con.execute("SELECT max(date) FROM yh.prices WHERE symbol=?", [tk]).fetchone()
            ymax = row[0] if row else None
            if ymax:
                # The warehouse's close is RESTATED onto today's share basis; option strikes
                # are quoted AS TRADED. Convert back with the splits that happened after each
                # date (CRWD's 4:1 on 2026-07-02 would otherwise pick strikes 4× too small).
                splits = con.execute(
                    "SELECT date, ratio FROM yh.splits WHERE symbol=? ORDER BY date", [tk]).fetchall()

                def as_traded(day_iso, close):
                    f = 1.0
                    for sd, ratio in splits:
                        if sd.isoformat() > day_iso:
                            f *= ratio
                    return close * f

                px = {d.isoformat(): as_traded(d.isoformat(), c) for d, c in con.execute(
                    "SELECT date, close FROM yh.prices WHERE symbol=?", [tk]).fetchall()}
        # Fetch from Alpaca: the whole series when the warehouse lacks the symbol, else the tail
        want_start = "2020-01-01" if ymax is None else (ymax + dt.timedelta(days=1)).isoformat()
        bars = []
        try:
            u = get(f"https://data.alpaca.markets/v2/stocks/{alpaca_sym(tk)}/bars?timeframe=1Day&start={want_start}&limit=10000")
            # `bars` is null (not absent) for a symbol with no data in the window — probed
            # live on an invalid ticker and a delisted one. `u.get("bars", [])` returns None
            # for a present-but-null key, so the loop below raised TypeError OUTSIDE the try
            # and killed the run — after the DELETE above had already removed the stored
            # tail. Guard the shape, exactly as the option-bars path does.
            bars = u.get("bars") or []
        except Exception as e:
            # This fetch only REFRESHES prices; it is not what the ticker was queued for.
            # `continue` here abandoned the whole ticker, so the --dense --gapfill pass it
            # was launched for never ran. Measured 2026-10-03: one ~20-minute network window
            # cost 255 of 413 tickers their gapfill, and 254 of them were already fully
            # priced from the warehouse (px above) — nothing was actually wrong with them.
            # The cause is carried in full: repr(e)[:80] stopped at the URL and hid it.
            print(f"{tk}: stock bars fail {repr(e)[:200]} "
                  f"— continuing on {len(px)} stored closes", flush=True)
        if bars:
            # Store only what the warehouse does not already hold (no second copy of
            # history), and prune any earlier duplicate download for the same span. Guarded
            # by `if bars` because the DELETE would otherwise remove the stored tail with no
            # replacement to insert — a failed refresh must not cost the rows it failed to
            # refresh.
            con.execute("DELETE FROM mh.underlying_daily WHERE symbol=? AND source LIKE 'alpaca%' AND day >= ?",
                        [tk, want_start])
            for b in bars:
                con.execute("INSERT INTO mh.underlying_daily VALUES (?,?,?,?,?,?,NULL,?,NULL,'alpaca_stock')",
                            [b["t"][:10], tk, b["o"], b["h"], b["l"], b["c"], b["v"]])
                px[b["t"][:10]] = b["c"]
            if yh and ymax:
                con.execute("DELETE FROM mh.underlying_daily WHERE symbol=? AND source LIKE 'alpaca%' AND day < ?",
                            [tk, want_start])   # drop the duplicate history the warehouse already holds
        if not px:
            print(f"{tk}: no stock bars"); continue
        src_note = f"warehouse->{ymax} + {len(bars)} tail" if ymax else f"Alpaca full ({len(bars)})"
        print(f"{tk}: prices from {src_note}", flush=True)

        exps = third_fridays(start, end)
        con.execute("""CREATE TABLE IF NOT EXISTS mh.option_bars_checks (
            source TEXT, underlying TEXT, expiry DATE, bars INTEGER, checked_utc TIMESTAMP,
            PRIMARY KEY (source, underlying, expiry))""")
        # `mhro` (the merged file) exists ONLY when db.connect() was given staging=. On the
        # first-choice history="rw" path the database attaches as `mh` and there is no mhro,
        # so this raised CatalogException and killed the run on the first ticker. It survived
        # in production purely because another job always held market_history open, forcing
        # the staging fallback — i.e. the script only ran when its own preferred path failed.
        def _read(sql_with_mhro, sql_mh_only, params):
            """Read staging UNION merged when `mhro` exists, else staging alone.

            `mhro` is attached ONLY on the staging= path. On the first-choice history="rw"
            path the database itself is `mh`, so referencing mhro raised CatalogException and
            killed the run on the first ticker — the script only ever worked when its
            preferred path failed. Falling back keeps both paths usable.
            """
            # Each SQL gets a parameter list sized to ITS placeholders. The mhro-UNION form
            # takes four (source, underlying) twice; the fallback takes two. Passing the
            # four-slot list to both raised "Parameter argument/count mismatch, identifiers
            # of the excess parameters: 3, 4" and killed the run -- the code review flagged
            # this exact line and the fix landed on the second call site, not this one.
            for sql, ps in ((sql_with_mhro, params), (sql_mh_only, params[:2])):
                try:
                    return con.execute(sql, ps).fetchall()
                except Exception:
                    continue
            return []

        done = {r[0] for r in _read(
            "SELECT DISTINCT expiry FROM (SELECT expiry FROM mhro.option_bars "
            "WHERE source=? AND underlying=? UNION SELECT expiry FROM mh.option_bars "
            "WHERE source=? AND underlying=?)",
            "SELECT DISTINCT expiry FROM mh.option_bars WHERE source=? AND underlying=?",
            [SRC, tk, SRC, tk])}
        # expiries that returned nothing are remembered too — a vintage expiry will never
        # gain bars, so re-requesting it every run only burns API quota
        done |= {r[0] for r in _read(
            """SELECT expiry FROM mhro.option_bars_checks
               WHERE source=? AND underlying=? AND bars=0 AND expiry < CURRENT_DATE - 7""",
            """SELECT expiry FROM mh.option_bars_checks
               WHERE source=? AND underlying=? AND bars=0 AND expiry < CURRENT_DATE - 7""",
            [SRC, tk])}
        n_new = n_skip = n_gap = 0
        # ── PHASE 1: plan every expiry (pure arithmetic, no I/O) ─────────────────────────
        # With --gapfill the band is computed BEFORE the skip decision, because the decision
        # depends on it: "we already have this expiry" is only meaningful relative to the
        # strikes THIS run is asking for. Under --bulk we hold 0.80-1.20; under --dense we
        # want 0.70-1.30, so the same expiry can be complete under one and short under the
        # other, and the plain skip list cannot tell the difference.
        anchors = ANCHORS_BULK if args.bulk else (ANCHORS_DENSE if args.dense else ANCHORS)
        # ONE coverage query per ticker, not one per expiry.
        #
        # The per-expiry form asked `... WHERE underlying=? AND expiry=?` against mhro, which
        # is a VIEW over the 8.1M-row snapshot with no index on (underlying, expiry) -- so
        # each of the ~37 expiries scanned the whole table. Measured: 127 s per ticker, an
        # ETA of 14.5 h for a run whose request count (15,281, one per expiry) puts the
        # Alpaca-rate floor at 76 MINUTES. The gap-fill check was costing 11x the download it
        # was meant to make cheap. Pulling every (expiry, symbol) for the ticker in one pass
        # and bucketing in Python removes 36 of the 37 scans.
        have_by_exp = {}
        if args.gapfill and not args.force:
            for _sym, _exp in _read(
                "SELECT symbol, expiry FROM (SELECT symbol, expiry FROM mhro.option_bars "
                "WHERE source=? AND underlying=? "
                "UNION SELECT symbol, expiry FROM mh.option_bars "
                "WHERE source=? AND underlying=?)",
                "SELECT symbol, expiry FROM mh.option_bars WHERE source=? AND underlying=?",
                [SRC, tk, SRC, tk],
            ):
                have_by_exp.setdefault(_exp, set()).add(_sym)
        plan = []
        for exp in exps:
            anchor_d = exp - dt.timedelta(days=45)
            cands = [d for d in px if d <= anchor_d.isoformat()]
            if not cands: continue
            akey = max(cands); S0 = px[akey]
            strikes = sorted({round_strike(S0 * a) for a in anchors})
            segments = windows(tk, start, min(exp, clock.today()-dt.timedelta(days=1)))
            syms = [contract(tk, root, exp, cp, k) for root, _, _ in segments
                    for k in strikes for cp in ("C", "P")]
            if exp in done and not args.force:
                if not args.gapfill:
                    n_skip += 1; continue
                # GAP-FILL ONLY WHAT CAN STILL CHANGE.
                #
                # A dead expiry's rows are final. Its sub-90% coverage is almost always
                # strikes that never traded, and re-asking for them returns nothing: measured
                # 2026-10-04, 14 names x ~36 dead expiries ~= 500 requests, +0 rows (direct
                # probe on AAPL 2024-03-15: 24/24 stored symbols answered, but only 2 of 40
                # missing inner strikes existed, 0 of 40 outer). A LIVE expiry is the
                # opposite case -- strikes trade as it matures, so its stored band goes stale
                # between runs and must be re-asked. The 7-day grace matches the checks-skip
                # rule above: an expiry that ended this week may still be missing its final
                # days' bars, one that ended earlier cannot gain any. --gapfill-all restores
                # the old re-check-everything behavior for history recovery.
                if exp < clock.today() - dt.timedelta(days=7) and not args.gapfill_all:
                    n_skip += 1; continue
                # The expiry is in the skip list. Is it actually short of the strikes we now
                # want? Answered from the per-ticker map built above -- see the comment there
                # for why this is not a query.
                have = have_by_exp.get(exp, set())
                cov = len(have.intersection(syms)) / len(syms)
                if cov >= args.gapfill_min:
                    n_skip += 1; continue
                # FETCH ONLY WHAT WE LACK.
                #
                # Re-requesting the full band re-downloads every strike we already hold, and
                # the response is one call for all symbols — so the duplication is invisible
                # in the log but real in the quota. Narrowing the list is what makes this a
                # gap-fill rather than a re-download with extra steps: measured here, the
                # stored band is 42 of the 122 symbols a dense expiry asks for, so this
                # fetches 80 and re-fetches none.
                #
                # The threshold above still governs SKIPPING, because a strike that never
                # trades is missing forever and would otherwise be re-requested every run.
                missing = [s for s in syms if s not in have]
                if not missing:
                    n_skip += 1; continue
                syms = missing
                n_gap += 1
            plan.append((exp, akey, S0, strikes, [syms[i:i + SYMBOLS_PER_REQUEST]
                         for i in range(0, len(syms), SYMBOLS_PER_REQUEST)]))
        work = []
        for e, _a, _s, strikes, batches in plan:
            selected = {symbol for batch in batches for symbol in batch}
            for root, first, last in windows(tk, start, min(e, clock.today()-dt.timedelta(days=1))):
                segment = [contract(tk, root, e, cp, k) for k in strikes for cp in ("C", "P")]
                segment = [symbol for symbol in segment if symbol in selected]
                work.extend((e, segment[i:i+SYMBOLS_PER_REQUEST], first, last)
                            for i in range(0, len(segment), SYMBOLS_PER_REQUEST))

        def _fetch(item):
            exp, batch, first, last = item
            first, last = utc_day_bounds(first, last)
            # Never let one batch raise: `ex.map()` re-raises the first exception when its
            # results are consumed, so an unguarded failure aborts the WHOLE ticker loop.
            #
            # But return None, NOT {} — an empty dict means "the API answered, there is
            # nothing there", which is a fact worth remembering. A failure means "we do
            # not know", which must never be remembered. Collapsing the two wrote
            # bars=0 into option_bars_checks for expiries that had simply failed, and
            # once merged those are skipped forever: NVDA 2025-01-17 and 2025-02-21 were
            # recorded as empty (0 bars) while their neighbours held 4,361 and 4,533.
            # That is the exact failure this project forbids — a transient error hardened
            # into a permanent "no data".
            try:
                # FOLLOW PAGINATION. One page caps at 10,000 bars, and a single expiry
                # exceeds that easily — the --dense grid alone is 122 contracts x ~200
                # days. Ignoring next_page_token stored a truncated series as if complete
                # AND wrote its count to option_bars_checks, which then skipped the expiry
                # forever. Silent truncation is worse than a failure: a failure is retried.
                out, token = {}, None
                while True:
                    url = ("https://data.alpaca.markets/v1beta1/options/bars?symbols=" +
                           ",".join(batch) +
                           f"&timeframe=1Day&start={first}&end={last}&limit=10000")
                    if token:
                        url += f"&page_token={token}"
                    r = get(url)
                    for s_, bs_ in (r.get("bars") or {}).items():
                        out.setdefault(s_, []).extend(b for b in bs_
                            if first[:10] <= b["t"][:10] <= last[:10])
                    token = r.get("next_page_token")
                    if not token:
                        return exp, out
            except Exception as e:
                print(f"  {tk} {exp}: batch FAILED (not recorded) ({repr(e)[:70]})",
                      flush=True)
                return exp, None

        # ── PHASE 2: ONE pool for every expiry's batches ────────────────────────────────
        # Fetch first, write after. Writes stay on this thread (DuckDB has one writer per
        # connection); only the network work is parallel. Results are regrouped by expiry
        # so the write path below is unchanged.
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            fetched = list(ex.map(_fetch, work))
        by_exp = {}
        for _e, _r in fetched:
            by_exp.setdefault(_e, []).append(_r)

        # ── PHASE 3: write each expiry ──────────────────────────────────────────────────
        for exp, akey, S0, strikes, batches in plan:
            total = 0
            results = by_exp.get(exp, [])

            incomplete = any(res is None for res in results)
            for res in results:
                try:
                    buf = []
                    for s, bs in (res or {}).items():
                        cp = s[-9]; K = int(s[-8:]) / 1000
                        for b in bs:
                            buf.append([SRC, s, tk, exp, K, cp, b["t"][:10], b["o"], b["h"],
                                        b["l"], b["c"], b["v"], b.get("vw")])
                            total += 1
                    write_bars(con, buf)     # one set-based statement per 500 rows
                except Exception as e:
                    print(f"  {tk} {exp} batch fail {repr(e)[:80]}")
                # get_json paces each HTTP attempt; writes consume no request slots.
            n_new += total
            # Record an expiry ONLY when the whole set answered. A partial result must stay
            # unrecorded so the next run re-requests it — "we know it is empty" and "we
            # failed to ask" are different facts and only the first may be memoised.
            if incomplete:
                print(f"  {tk} {exp}: INCOMPLETE — checks row withheld, will re-request",
                      flush=True)
            else:
                con.execute(
                    """INSERT OR REPLACE INTO mh.option_bars_checks VALUES (?,?,?,?,now())""",
                    [SRC, tk, exp, total])
            print(f"  {tk} {exp}: {total} bars (anchor {akey} S={S0:.0f}, {len(strikes)} strikes)", flush=True)
            status("download_option_bars", ticker=tk, expiry=str(exp), bars_total=n_new)
        print(f"{tk}: +{n_new} option bars, {n_skip} expiries already present"
              + (f", {n_gap} re-fetched for missing strikes" if n_gap else ""), flush=True)
        status("download_option_bars", ticker=tk, ticker_done=True, tickers_finished=idx + 1,
               tickers_total=len(tickers), bars_total=n_new)
    con.close()
    print("done")


if __name__ == "__main__":
    main()
