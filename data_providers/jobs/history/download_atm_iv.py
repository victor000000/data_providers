import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""download_atm_iv.py — ATM ±2 strike IV history from Alpaca option bars (staging -> merge).

What this adds that the IBKR sweep cannot: IBKR's OPTION_IMPLIED_VOLATILITY is a single
rolling 30-day number per symbol per day. This returns IV at a configurable band (ATM ±--strikes), which is
the volatility smile — and it is computed with our own solver from raw bars, so it is an
independent second opinion to validate `bs.py` and the vendor series against (checklist F3).

Why it is affordable: for IVR/IVP we need one IV per day, not a full chain. So for each
monthly expiry E we fetch only the ~30-day window [E-42, E-18] — the days on which E IS the
front 30-day option — at a handful of candidate strikes around spot. One batched request per
expiry (~24 per name) instead of one per day.

Strikes are CANDIDATES, not guesses: the listed increment (0.5 / 1 / 2.5 / 5 / 10) depends on
the price level and we do not know it a priori, so we request a spread of plausible strikes
and keep the five nearest the money that actually returned bars. A wrong guess costs nothing;
an empty contract simply returns no bars (verified).

Staging writes to staging/atm_iv.duckdb; rows reach iv_history on merge_staging.py.

    uv run python apps/screener/download_atm_iv.py \
        [--tickers-file plans/universe_top1000.txt] [--months 24] [--workers 4]
"""
import argparse
import datetime as dt
import json
import math
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from data_providers.jobs.history.option_universe import (candidates, increments,  # noqa: E402
                                           monthly_expiries, occ)
from data_providers.data import clock  # noqa: E402
from data_providers.compute import bs  # noqa: E402
from data_providers.data import db  # noqa: E402
from data_providers.shared.net import status  # noqa: E402

ROOT = bootstrap.ROOT
from data_providers.credentials import AlpacaHeaders
HDR = AlpacaHeaders()
SRC = "alpaca_atm"
from data_providers.shared.rates import R_US as R
BATCH = 300                   # Alpaca accepted 300 in testing (verified 2026-10-01)
EMPTY_MEMORY_DAYS = 30        # how long "this symbol has no options" is trusted
FULL_BAND_DAYS = 3            # a complete smile older than this no longer counts as covered,
                              # so the daily series keeps extending instead of freezing at
                              # the last day a symbol happened to be struck at full width


from data_providers.jobs.history.atm_smile_memory import read_smile, listed_strikes, atm_mark
from data_providers.jobs.history.option_root_history import requests, contract


def get(url):
    from data_providers.providers.alpaca_http import get_json
    return get_json(url, HDR, tries=6, base=2.0)


def fetch(syms, d0, d1):
    """One batched bars request -> {symbol: [bars]}, following pagination."""
    from data_providers.providers.alpaca_http import utc_day_bounds
    d0, d1 = utc_day_bounds(d0, d1)
    out, token = {}, None
    while True:
        url = ("https://data.alpaca.markets/v1beta1/options/bars?symbols=" + ",".join(syms)
               + f"&timeframe=1Day&start={d0}&end={d1}&limit=10000")
        if token:
            url += f"&page_token={token}"
        # get_json claims the shared slot for every HTTP attempt, page and retry.
        r = get(url)
        bars = r.get("bars", {})
        if isinstance(bars, dict):
            for s, bs_ in bars.items():
                out.setdefault(s, []).extend(bs_)
        token = r.get("next_page_token")
        if not token:
            return out


def write_rows(con, table, cols, rows, chunk=500):
    """Set-based INSERT, not one execute() per row.

    The same measurement that fixed download_option_bars.py applies here: a single-row
    con.execute() costs ~12 ms (a whole transaction, parse, plan and PK index update per
    row), so this sweep's ~2,000-2,800 smile rows per ticker were ~25-35 s of INSERT alone
    — that, not the network, was its 45 s/ticker. executemany is no better (still one txn
    per row); INSERT ... SELECT FROM (VALUES ...) is ~10x. Chunked at 500 to stay well
    inside the parser's placeholder limit.
    """
    ncol = len(cols)
    collist = ",".join(cols)
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        values = ",".join(["(" + ",".join(["?"] * ncol) + ")"] * len(part))
        con.execute(
            f"INSERT INTO {table} ({collist}) "
            f"SELECT * FROM (VALUES {values}) AS v({collist}) ON CONFLICT DO NOTHING",
            [x for r in part for x in r])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers-file", default=str(ROOT / "plans" / "universe_top1000.txt"))
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="stop after N tickers (0 = all)")
    ap.add_argument("--strikes", type=int, default=5, choices=range(1, 51),
                    help="strikes either side of ATM; 2 = the old 5-point band, 5 = 11 points. "
                         "Validated >=1: a negative value makes the band EMPTY, every symbol "
                         "finishes with n=0, and that writes bars=0 to the completion memory -- "
                         "meaning 'the vendor has nothing', trusted for 30 days.")
    ap.add_argument("--gapfill", action="store_true",
                    help="request only the (expiry, strike, side) we do NOT already hold in "
                         "option_greeks_daily, instead of the whole band")
    ap.add_argument("--force", action="store_true", help="walk covered tickers; refills an OLD hole")
    ap.add_argument("--retry-empty", action="store_true", help="Retry prior empty receipts after repairing missing inputs")
    ap.add_argument("--recompute", action="store_true", help="Recompute smiles without cached Greeks after an input correction")
    ap.add_argument("--staging", default="atm_iv",
                    help="staging key; a second concurrent run needs its own file")
    args = ap.parse_args()
    if args.recompute and args.gapfill:
        ap.error('--recompute cannot reuse Greeks through --gapfill')

    try:
        from data_providers.shared import files
        tickers = [t.upper() for t in files.safe_read_ticker_file(args.tickers_file)]
    except (FileNotFoundError, ValueError, OSError) as e:
        print(f"Error: {e}")
        return
    if args.limit:
        tickers = tickers[:args.limit]
    con = db.connect(log=None, staging=args.staging, universe=True, snapshot=True)
    today = clock.today()
    start = today - dt.timedelta(days=int(args.months * 30.5))
    # Reach FORWARD past today. The 30-day window of an expiry E is [E-42, E-18], so the
    # expiry that carries TODAY's 30-day IV is the one ~30 days out — a future expiry.
    # Stopping at today() truncated the series at the last window that had already closed:
    # the first run ended 2026-08-31, a month stale, exactly the 21-45 DTE stretch the
    # screen trades. Future days simply have no bars yet; the window guard skips them.
    exps = monthly_expiries(start, today + dt.timedelta(days=60))
    print(f"{len(tickers)} names · {len(exps)} expiries · "
          f"ATM±{args.strikes} ({2 * args.strikes + 1} strikes) · source={SRC}", flush=True)

    def remember(tk, bars, oldest, newest):
        """Write the completion memory for one ticker (staging; merged by merge_staging.py).

        bars=None marks a sweep IN PROGRESS; a real count (0 or n) marks a finished one.
        NEVER call this after a failed fetch with anything but None — a failure recorded as
        "no data" is the one thing this project forbids, and it is unrecoverable once merged.
        """
        try:
            con.execute("INSERT OR REPLACE INTO mh.iv_history_checks VALUES (?,?,?,?,?,now())",
                        [SRC, tk, oldest, newest, bars])
        except Exception as e:
            print(f"{tk}: checks write failed {repr(e)[:70]}", flush=True)

    # Reuse first. Coverage is a DATE question: a ticker whose series reaches the newest
    # session we hold is not re-fetched.
    #
    # IT WAS A WIDTH QUESTION, AND THAT CANNOT WORK. The test used to demand a full-width
    # band — 2*strikes+1 distinct strikes on one recent session — so that widening --strikes
    # would re-strike the symbols that had not been widened yet. But the writer stores only
    # the strikes that TRADED on that day (a fixed-offset strike that did not print is an
    # honest gap, not a missing row), so a liquid name measures 5-7 strikes wide, never 11.
    # Measured 2026-10-11: 215 of 6,000 tickers qualified, so the 413-name universe was
    # re-walked for 1h20m and the merge folded in 135,201 greeks + 29,002 iv rows for +1 and
    # +0 NEW — every row it paid for was already on disk. A test nothing can satisfy is a
    # re-download of the whole universe with extra steps.
    #
    # (Its history is worth keeping: the FIRST version keyed on symbol alone with no width
    # and no date, so a widening run skipped every symbol swept narrow and reported success,
    # and a symbol skipped once was skipped forever — the series stopped extending silently.
    # The date test below is what makes a re-run over unchanged data a genuine no-op.)
    #
    # Widening is a deliberate act: --force, with --strikes. Not something a coverage test
    # should silently re-attempt on every run.
    # ANCHORED TO THE DATA, NOT TO THE CALENDAR. `today - FULL_BAND_DAYS` looks equivalent
    # and is not: whenever the newest day we hold lags the wall clock by more than
    # FULL_BAND_DAYS — every weekend, every vendor delay, and 10 straight days here — NO
    # ticker can satisfy the cutoff, the skip set is empty by construction, and the run
    # re-downloads the entire universe. Measured 2026-10-03: greeks max(day) 2026-09-23,
    # cutoff 2026-09-30, and 0 of 1000 tickers qualified. A second pass over unchanged data
    # added +0 rows to every table, which is the duplication this anchor exists to prevent.
    #
    # Anchoring to max(day) preserves the intent exactly — a symbol is re-struck once the
    # series has moved on — while making a re-run over the same data a genuine no-op. That
    # is what the query below now tests, on its own, with no width condition to fail.
    try:
        _newest = con.execute(
            """SELECT max(day) FROM (
                   SELECT day FROM mh.option_greeks_daily WHERE source=?
                    UNION ALL
                   SELECT day FROM mhro.option_greeks_daily WHERE source=?)""",
            [SRC, SRC]).fetchone()[0]
    except Exception:
        _newest = None
    band_cutoff = (_newest or today) - dt.timedelta(days=FULL_BAND_DAYS)
    try:
        have = {r[0] for r in con.execute(
            """SELECT underlying FROM (
                   SELECT underlying, day FROM mh.option_greeks_daily WHERE source=?
                    UNION ALL
                   SELECT underlying, day FROM mhro.option_greeks_daily WHERE source=?)
               GROUP BY underlying HAVING max(day) >= ?""",
            [SRC, SRC, band_cutoff]).fetchall()}
    except Exception as e:
        print(f"!! skip-set read failed ({repr(e)[:80]}) — falling back to an EMPTY skip "
              f"set: every symbol will be re-walked this run", flush=True)
        have = set()

    # Completion memory, from iv_history_checks (staging UNION merged):
    #   bars IS NULL  — a previous run started this symbol and died mid-write. Its staged
    #                   rows are PARTIAL, so the symbol must be re-fetched rather than
    #                   skipped forever on the strength of "it has some rows".
    #   bars = 0      — the vendor genuinely has nothing here (option-less ticker, no usable
    #                   expiries). Without this the sweep re-walks it every run and never
    #                   converges; it is trusted for EMPTY_MEMORY_DAYS, not forever, since a
    #                   new listing can gain options later.
    # A FAILED fetch is recorded as neither — it stays absent and is retried next pass.
    try:
        mem = con.execute(
            """SELECT underlying, bars, checked_utc FROM (
                   SELECT underlying, bars, checked_utc FROM mh.iv_history_checks
                    WHERE source=?
                   UNION ALL
                   SELECT underlying, bars, checked_utc FROM mhro.iv_history_checks
                    WHERE source=?)""", [SRC, SRC]).fetchall()
    except Exception:
        try:
            mem = con.execute("SELECT underlying, bars, checked_utc FROM mh.iv_history_checks "
                              "WHERE source=?", [SRC]).fetchall()
        except Exception as e:
            print(f"!! checks memory read failed {repr(e)[:70]} — empties will be re-walked",
                  flush=True)
            mem = []
    unfinished = {u for u, bars, _ in mem if bars is None}
    cutoff = today - dt.timedelta(days=EMPTY_MEMORY_DAYS)
    empty = {u for u, bars, ts in mem
             if bars == 0 and ts is not None and ts.date() >= cutoff}
    if args.retry_empty:
        empty = set()

    grand = 0
    grand_smile = 0
    n_skip = 0
    for idx, tk in enumerate(tickers):
        if (tk in have and tk not in unfinished and not args.force) or tk in empty:
            n_skip += 1
            # TWO reasons, meaning opposite things: `empty` is a memorised "we got no usable
            # rows here" (bars=0), `have` is a band we already hold. Both printed "already
            # covered", so on 2026-10-07 37 zero-row tickers (GLDM, FNGU, PSLV, STIP, ...)
            # read as covered -- the log said the data was in hand and the query said absent.
            # And "no usable rows" is not "no options": STIP has listed options and Alpaca
            # returns bars for a couple, but 2 contracts and 3 bars over five weeks prices
            # nothing -- re-fetched with its memory cleared, it produced 0 rows again.
            why = "already covered" if tk in have else "no usable data (memorised)"
            print(f"{tk}: skip ({why}; {n_skip} skipped so far)", flush=True)
            status("download_atm_iv", ticker=tk, done=idx + 1, total=len(tickers),
                   rows=grand, skipped=n_skip)
            continue
        try:
            splits = con.execute("SELECT date, ratio FROM yh.splits WHERE symbol=? ORDER BY date",
                                 [tk]).fetchall()
        except Exception:
            splits = []

        broker_days = set()
        def as_traded(day_iso, close):
            if day_iso in broker_days:
                return close  # Broker RTH bars already use the historical trading basis.
            f = 1.0
            for sd, ratio in splits:
                if sd.isoformat() > day_iso:
                    f *= ratio
            return close * f

        try:
            px = {d.isoformat(): c for d, c in con.execute(
                "SELECT date, close FROM yh.prices WHERE symbol=? AND date >= ?",
                [tk, start - dt.timedelta(days=60)]).fetchall()}
            from data_providers.jobs.history.option_spots import add_broker_spots
            px, broker_days = add_broker_spots(con, tk, start-dt.timedelta(days=60),
                                              today-dt.timedelta(days=1), px)
            if broker_days:
                print(f"{tk}: broker RTH spot inputs for {sorted(broker_days)} (Yahoo gaps)",flush=True)
        except Exception as e:
            print(f"{tk}: warehouse read failed {repr(e)[:50]}", flush=True); continue
        if not px:
            print(f"{tk}: missing warehouse prices — retry after the price input is repaired", flush=True); continue

        # ── gap-fill: what do we ALREADY hold for this ticker? ───────────────────────────
        #
        # Read once per ticker, not per expiry: `mhro` is a view over the market_history
        # snapshot with no index on (underlying), so a per-expiry query would re-scan it 24
        # times — the same mistake that cost 127s per ticker in the bars downloader.
        #
        # Actual OCC symbols distinguish historical issuer roots and reused tickers.
        #
        # OFF BY DEFAULT, deliberately. A normal run WANTS to re-strike the whole band —
        # that is how the daily series extends, and the ticker-level skip above decides when
        # it is due. Gap-fill is for the other job: widening the band (±5 -> ±7) or
        # recovering batches a network failure dropped. Those add strikes we lack and must
        # not re-request the eleven we hold.
        # Keyed to the LAST DAY WE HOLD for that contract, not merely "do we hold it".
        #
        # A key of (expiry, strike, side) alone is wrong in a way that hides for a while:
        # a window END MOVES. d1 = min(expiry-18d, today-1d), so for any expiry more than
        # 19 days out the window advances one day per day — and a strike held yesterday
        # would count as held today, the contract would never be re-requested, and the
        # series would silently stop extending for every strike we already had. The
        # symptom would be an IV series frozen at the day gap-fill was first used, which
        # reads exactly like a vendor problem.
        #
        # Comparing against this expiry's own d1 keeps the guarantee intact: re-request
        # whenever the window reaches past what we hold, skip only when it does not.
        held = {}
        if args.gapfill:
            try:
                # .fetchall(), not the bare execute(): duckdb's execute() returns the
                # CONNECTION for chaining, so iterating it raises "not iterable" — which the
                # except below turns into a silent full-band fetch. Caught by running it.
                held = {symbol: str(mx) for symbol, mx in con.execute(
                    """SELECT symbol, max(day) FROM (
                           SELECT symbol, day FROM mhro.option_greeks_daily
                            WHERE source=? AND underlying=?
                           UNION ALL
                           SELECT symbol, day FROM mh.option_greeks_daily
                            WHERE source=? AND underlying=?)
                        GROUP BY 1""", [SRC, tk, SRC, tk]).fetchall()}
            except Exception as e:
                # Not silent, and not fatal: falling back to the full band re-requests data
                # we hold (wasteful) whereas skipping would drop strikes we need (wrong).
                print(f"{tk}: gap-fill read failed ({repr(e)[:60]}) — fetching the full band",
                      flush=True)

        # one job per expiry: the ~30-day window on which that expiry is the front month
        jobs, keep = [], {}
        for exp in exps:
            cand = [d for d in px if d <= (exp - dt.timedelta(days=30)).isoformat()]
            if not cand:
                continue
            akey = max(cand)
            S0 = as_traded(akey, px[akey])
            if S0 <= 0:
                continue
            # Margin scales with the band: the lattice is anchored on S0 (the close at
            # expiry-30d) while the band tracks each day's own spot across a 24-day
            # window, so a constant +2 increments of drift tolerance shrinks in
            # relative terms as the band widens, and the outer strikes come back
            # absent with no error and no count.
            ks = candidates(S0, reach=args.strikes + max(3, args.strikes // 2 + 3))
            d0 = max(exp - dt.timedelta(days=42), start).isoformat()
            # Clamp to YESTERDAY, not today. Alpaca returns HTTP 403 "OPRA agreement is not
            # signed" for any options request whose window includes the current date —
            # verified 2026-10-01 across offsets 0/1/5/10/15/16/20/30, where only offset 0
            # failed. Without the clamp it is not an error but a SILENT EMPTY: retry() gave
            # up after 4 tries, every batch returned {}, and the ticker finished reporting
            # "0 rows" as though the contracts simply had no data.
            d1 = min(exp - dt.timedelta(days=18), dt.date.fromisoformat(max(px)),   # max(px)
                     today - dt.timedelta(days=1)).isoformat()   # because markets shut at w/e
            # Fetch unless we hold this contract THROUGH the window's end (d1, an ISO date
            # string — string comparison is date comparison for ISO). Held-but-stale means
            # the window has moved past what we have, which is the case gap-fill must not
            # swallow.
            # START EACH CONTRACT WHERE WE STOPPED HOLDING IT, not at d0. The old code asked
            # for the whole window [d0, d1] as soon as a contract was short of d1, so a
            # contract carried through 09-28 was re-requested from 09-04 — and because d1
            # advances one day per day, EVERY contract is short of d1 every day, and the whole
            # ~24-day window was re-bought to gain one day. Measured 2026-10-10: 371,906
            # option_greeks_daily rows staged for +14,349 new, i.e. 96% of the download was
            # already on disk — the exact figure update_all.sh credits --gapfill with avoiding.
            #
            for root, syms, first, last in requests(tk, exp, ks, d0, d1, held):
                keep[exp, root] = (ks, S0, d0, d1)
                jobs.append((exp, root, syms, first, last))
        if not jobs:
            # bars=0 must mean the VENDOR has nothing; a frozen tail is OUR defect (20 lost, 10-10).
            fresh = max(px) >= band_cutoff.isoformat()
            if fresh:
                remember(tk, 0, None, None)
            print(f"{tk}: no usable expiries — {'remembered empty' if fresh else 'prices end ' + max(px) + ' — NOT remembered'}", flush=True); continue

        # From here the ticker can leave PARTIAL rows if this process is killed (rc=137 is
        # routine). Mark the sweep in progress (bars IS NULL) so a later run re-fetches the
        # symbol instead of trusting a half-written series; the marker is replaced with the
        # real count only when the whole ticker finishes without a failed batch.
        remember(tk, None, None, None)

        # Batch PER EXPIRY, not across all of them. The old code sent every symbol with the
        # widest window in the set (min d0 .. max d1 = two full years), so each contract came
        # back with ~500 daily bars when the strategy needs only its own ~24-day window. That
        # inflated every response 20x and let pagination, not the symbol count, drive the
        # request total (~63 per name for data that fits in ~26). Grouping by expiry keeps
        # each request to a single page.
        batches = []
        for _exp, _root, syms_e, d0_e, d1_e in jobs:
            for i in range(0, len(syms_e), BATCH):
                batches.append((syms_e[i:i + BATCH], d0_e, d1_e))
        bars_by_sym = {}
        failed_batches = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(fetch, syms, d0, d1): syms for syms, d0, d1 in batches}
            for fut in as_completed(futs):
                try:
                    for s, bl in fut.result().items():
                        bars_by_sym.setdefault(s, []).extend(bl)
                except Exception as e:
                    # Print the TAIL. The message is RuntimeError("<url>") failed after
                    # 4 tries: <real error>, so [:50] cuts the URL and hides the cause — an
                    # entire class of failure was invisible for hours because of this.
                    failed_batches += 1
                    print(f"  {tk}: batch fail ...{repr(e)[-220:]}", flush=True)

        n = 0
        n_smile = 0
        days_seen = []
        for exp, root, syms, d0, d1 in jobs:
            ks, S0, w0, w1 = keep[exp, root]
            rows = []
            memory = {} if args.recompute else read_smile(con, SRC, tk, exp, d0, d1)
            smile = []          # the five-point smile for this expiry -> option_greeks_daily
            # The window is the point. A batch fetches every bar the contract has, so without
            # this filter we would solve IV on a 200-DTE contract and file it beside a 30-DTE
            # one — the column carries no tenor, so IVR would rank incomparable numbers
            # against each other. Keep T in ~[18, 42] days: a 30-day constant maturity.
            for day in sorted({b["t"][:10] for s in syms for b in bars_by_sym.get(s, [])}):
                if not (d0 <= day <= d1) or day not in px:
                    continue
                cached = memory.get(day, {})
                S = as_traded(day, px[day])
                d = dt.date.fromisoformat(day)
                T = (exp - d).days / 365.0
                if T <= 0 or S <= 0:
                    continue
                # the five strikes nearest the money that actually traded
                live = {}
                for cp in ("C", "P"):
                    for k in ks:
                        for b in bars_by_sym.get(contract(tk, root, exp, cp, k), []):
                            if b["t"][:10] == day and b.get("c") and b["c"] > 0:
                                live.setdefault(k, {})[cp] = b["c"]
                if not live:
                    continue
                # ATM and ATM ±2 — five strikes at FIXED OFFSETS from the money, not "the five
                # nearest that traded". Ranking by proximity looks equivalent and is not: on a
                # day when the +2 strike had no bar, the band slid down and filed a strike 25
                # points out as "ATM-2". Offsets come from the listed increment, so a strike
                # that did not trade that day is simply ABSENT — an honest gap in the smile
                # rather than a mislabelled point.
                ever = listed_strikes((k for k in ks if bars_by_sym.get(contract(tk, root, exp, "C", k))
                              or bars_by_sym.get(contract(tk, root, exp, "P", k))), cached)
                if not ever:
                    continue
                atm = min(ever, key=lambda k: abs(k - S))
                # Pick the increment by COVERAGE, not by the smallest gap.
                #
                # `ever` is the union of the three generated ladders (5/10/25 grids), so
                # consecutive gaps are small even when the venue lists only 10s and 25s --
                # `min(gaps)` then returns a step the exchange does not offer, and the band
                # built on it lands BETWEEN listed strikes. The smile comes back with holes
                # at interior offsets (±2, ±4) while still looking like a fixed-offset ladder,
                # which is worse than a narrow one: nothing downstream can tell.
                #
                # Measured on live staging data before this fix: of 45,104 (underlying,
                # expiry, day) groups, only 638 carried the full 11 strikes and 3,936 had
                # non-uniform spacing (e.g. BF-B [30, 35, 45]).
                #
                # So: score each PLAUSIBLE increment by how many of its band points actually
                # exist, and take the best. Ties go to the finer increment.
                inc = 0
                _best = -1
                for _c in increments(S0):
                    _pts = [atm + n * _c for n in range(-args.strikes, args.strikes + 1)]
                    _hit = sum(1 for x in _pts if x in ever)
                    if _hit > _best:
                        _best, inc = _hit, _c
                # WIDTH is a parameter, not a constant. The user asked for ATM ±1 and ±2
                # and then explicitly "not only ±2 strikes" — a fixed 5-point band was my
                # choice, not a requirement, and a 5-point smile cannot describe skew
                # curvature. Default ±5 (11 points); the cost is linear in width because
                # they ride the same batched request.
                band = ([atm] if inc <= 0
                        else [atm + n * inc for n in range(-args.strikes, args.strikes + 1)])
                solved = {}
                for k in band:
                    for cp in ("C", "P"):
                        p = live.get(k, {}).get(cp)   # a fixed-offset strike may not have traded
                        if not p:
                            continue
                        v = bs.implied_vol(p, S, k, T, R, cp == "C")
                        if not v:
                            continue
                        g = bs.bs_greeks(S, k, T, R, v, cp == "C")
                        sym = contract(tk, root, exp, cp, k)
                        smile.append([SRC, sym, tk, exp, k, cp, day, v,
                                      g[0], g[1], g[2], g[3], None])
                        solved[k, cp] = v
                mark = atm_mark(S, solved, cached)
                if mark is not None:
                    rows.append([day, tk, exp, mark[1], S, SRC])
            write_rows(con, "mh.iv_history",
                       ["day", "underlying", "expiry", "atm_iv", "spot", "source"], rows)
            write_rows(con, "mh.option_greeks_daily",
                       ["source", "symbol", "underlying", "expiry", "strike", "call_put",
                        "day", "iv", "delta", "gamma", "theta", "vega", "rho"], smile)
            n_smile += len(smile)
            n += len(rows)
            days_seen.extend(r[0] for r in rows)
        grand += n
        grand_smile += n_smile
        if failed_batches:
            # The ticker is PARTIAL. Leave the in-progress marker so the next pass re-fetches
            # it; writing a finished count here would freeze the missing expiries forever.
            print(f"{tk}: {failed_batches} batch(es) failed — left unmarked, will re-fetch "
                  f"({n} rows kept)", flush=True)
        else:
            remember(tk, n, min(days_seen) if days_seen else None,
                     max(days_seen) if days_seen else None)
        status("download_atm_iv", ticker=tk, done=idx + 1, total=len(tickers), rows=grand,
               skipped=n_skip)
        print(f"{tk}: {len(jobs)} expiries -> {grand:,} ATM rows, "
              f"{grand_smile:,} smile points cumulative ({n_skip} skipped)", flush=True)
    con.close()
    print(f"done: {grand:,} rows")


if __name__ == "__main__":
    main()
