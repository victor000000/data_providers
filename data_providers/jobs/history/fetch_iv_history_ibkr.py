import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""fetch_iv_history_ibkr.py — IBKR TWS 30-day IV history (OPTION_IMPLIED_VOLATILITY) -> iv_history.

Covers exactly what Alpaca CANNOT: indices (SPX/NDX/RUT/VIX) and futures (ES/NQ/CL/GC/ZB...),
plus an independent validator series for the tier-1 equity/ETF names.

The bar returned by whatToShow='OPTION_IMPLIED_VOLATILITY' is IBKR's rolling 30-day IV of the
underlying -> stored in iv_history.atm_iv with source='ibkr_tws30d'.

Idempotency (no re-download, no duplicates): a symbol whose newest row reaches within
--stale-days of the newest SESSION held is SKIPPED; otherwise fetch the full window and
replace that symbol's rows (DELETE-then-INSERT per symbol, as build_iv_history_cn.py does).

Universe file line formats:
    META               stock/ETF  -> Stock(sym, 'SMART', 'USD')
    IDX:SPX:CBOE       index      -> Index(sym, exch, 'USD')
    FUT:ES:CME         future     -> ContFuture(sym, exch)   (continuous, full 1y history)

Requires TWS running on the Windows host with API enabled (trusted IP 192.168.65.254),
IB Gateway live 4001 / paper 4002 (IB_PORT overrides; see datasources/tws.py).

Usage:
  uv run python apps/screener/fetch_iv_history_ibkr.py \
      --universe plans/ibkr_iv_universe.txt [--host host.docker.internal] [--port 4001]
      [--duration "2 Y"] [--stale-days 1] [--dry-run]
"""
import argparse
import datetime as dt
from data_providers.data import clock
from data_providers.providers import tws
from data_providers.jobs.history.ibkr_iv_request import historical_iv_bars
from data_providers.jobs.history.ibkr_iv_anchor import source_anchor
import pathlib
import sys
import time

from data_providers.data import db  # scripts/db.py — knows the three paths
from data_providers.shared.net import status, throttle  # shared pacing gate + progress files


def as_date(v):
    """Coerce a DuckDB value to datetime.date, or None.

    `iv_history` and `iv_history_checks` now have TWO writers with different conventions —
    this script (DATE) and download_atm_iv.py (ISO string) — so a column declared DATE can
    hand back either type depending on which attached file answered. Every comparison and
    every timedelta against these values must go through here or it is a TypeError waiting
    for the right symbol to come along.
    """
    import datetime as _dt
    if v is None:
        return None
    if isinstance(v, _dt.date) and not isinstance(v, _dt.datetime):
        return v
    if isinstance(v, _dt.datetime):
        return v.date()
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def duration_days(d):
    """'2 Y' -> 730. IBKR's durationStr grammar is '<n> <unit>' with S/D/W/M/Y."""
    try:
        n, unit = d.split()
        return int(float(n) * {"S": 1 / 86400, "D": 1, "W": 7, "M": 30, "Y": 365}[unit.upper()])
    except Exception:
        return 365


def gate(extra=0.0):
    """Wait for the shared IBKR slot, plus an optional extra pause.

    Rate is the registry default (60 per 600 s at 95% margin, one every 10.5 s). An earlier
    run of timeouts looked like pacing and was blamed on this rate — wrong. A probe from a
    fresh client id later returned Error 1102, "Connectivity between IBKR and Trader
    Workstation has been restored": the GATEWAY LINK had dropped, and every request stalled
    until its 90 s timeout, which the loop then logged as "no bars". Liquid names (ABT, NET,
    MPWR, PATH, XLU) silently became false negatives.

    The lesson is in `breaker()` below, not in the margin: slowing down would not have
    helped, and it costs hours. A stall that repeats is a connection problem, not a quota.
    """
    throttle("ibkr")
    if extra > 0:
        time.sleep(extra)


class breaker:
    """Pause the sweep when requests start failing in a row.

    `ib.isConnected()` stays True while TWS is up but its link to IBKR is down — exactly the
    2026-10-01 outage, where 31 consecutive requests timed out and were recorded as "no
    bars". A run of failures is the signal that the gateway, not the symbol, is the problem,
    so stop asking and give the link time to come back. Counted per symbol, since one dead
    symbol must never trip it.
    """

    LIMIT = 5
    PAUSE = 120

    def __init__(self):
        self.n = 0

    def ok(self):
        self.n = 0

    def failed(self, label):
        self.n += 1
        if self.n >= self.LIMIT:
            print(f"  !! {self.n} consecutive failures (last: {label}) — the IBKR link is "
                  f"probably down, not the symbols. Pausing {self.PAUSE}s.", flush=True)
            time.sleep(self.PAUSE)
            self.n = 0
            return True
        return False
from ib_async import IB, ContFuture, Index, Stock

ROOT = bootstrap.ROOT
SRC = "ibkr_tws30d"


def parse_line(line):
    """-> (label, contract) or None for comments/blanks."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("IDX:"):
        _, sym, exchs = line.split(":")          # IDX:RUT:RUSSELL,CBOE — try candidates in order
        return f"{sym}:IDX", [Index(sym, e, "USD") for e in exchs.split(",")]
    if line.startswith("FUT:"):
        _, sym, exch = line.split(":")
        return f"{sym}:{exch}", [ContFuture(sym, exch)]
    # IBKR spells class shares with a SPACE where our map (and OCC) use a hyphen: verified
    # against the live gateway 2026-10-02, Stock('BRK-B') and Stock('BRKB') both answer
    # Error 200 "no security definition" while Stock('BRK B') returns conId 72063691. Three
    # spellings, one of which resolves. The map stays hyphenated because that is the form
    # every other consumer wants; the translation belongs here, where the contract is built.
    return line, [Stock(line.replace("-", " "), "SMART", "USD")]


def fetch_iv(ib, label, contracts, duration):
    """Try each candidate contract (exchanges differ per index); first non-empty wins."""
    bars, last_error = [], None
    for c in contracts:
        try:
            bars = historical_iv_bars(ib, c, duration)
        except Exception as exc:
            last_error, bars = exc, []
        if bars:
            break
    if not bars and last_error is not None:
        raise last_error
    out = []
    for b in bars or []:
        d = b.date
        iso = d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]
        # a 30-day IV above 500% is a bad print, not a market: CL futures returned values
        # like 11021 (selfcheck 2026-10-01). Skip and count rather than store nonsense.
        if b.close is not None and 0 < b.close < 5:
            out.append((iso, float(b.close)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default=str(ROOT / "plans" / "ibkr_iv_universe.txt"))
    # Default None, not tws.PORT: an explicit --port still pins one, but with nothing
    # given we let tws.connect() try every TWS the user might be running. Defaulting to
    # a single port put the same wrong assumption in the CLI, where it is harder to see
    # -- the run failed on "connection refused" and looked like TWS was down.
    ap.add_argument("--host", default=tws.HOST)
    ap.add_argument("--port", type=int, default=None,
                    help="pin a port; omit to try Gateway (4001/4002) and desktop (7496/7497)")
    ap.add_argument("--client-id", type=int, default=71)
    # Added 2026-10-10 so the sweep can be SHARDED across processes: DuckDB allows one writer
    # per file, so N concurrent sweeps each need their own staging key, merged afterwards. The
    # one lever left once the rate gate stops binding — at IBKR_LIMIT=3600/600 a single process
    # ran 0.40/s, response-bound at ~2.5 s/symbol with 15x of its 6/s budget unused. Shards must
    # get DISJOINT universes or they duplicate each other's fetches.
    ap.add_argument("--staging", default="ibkr",
                    help="staging key; a concurrent shard needs its own file")
    ap.add_argument("--duration", default="2 Y")
    ap.add_argument("--stale-days", type=int, default=1,
                    help="skip a symbol whose newest row is this fresh (0 = always refresh)")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="EXTRA seconds on top of the shared gate. Pacing is owned by "
                         "net.LIMITS['ibkr'] (60 historical / 600 s -> 10.5 s per request, "
                         "shared across processes); leave this at 0")
    ap.add_argument("--dry-run", action="store_true", help="fetch + print only, no DB access")
    args = ap.parse_args()

    items = [x for x in (parse_line(l) for l in
                         pathlib.Path(args.universe).read_text().splitlines()) if x]
    if not items:
        print("empty universe"); return

    # Status namespaced by staging key: N shards wrote ONE file and `done` went BACKWARDS.
    _stat = "fetch_iv_history_ibkr" if args.staging == "ibkr" else f"fetch_iv_history_ibkr_{args.staging}"
    con = None if args.dry_run else db.connect(log=None, staging=args.staging, snapshot=True)
    today = clock.today()
    # Read the full source, not this shard's snapshot: an old-only shard must not
    # treat its own newest row as the market's newest observed session.
    anchor = source_anchor(SRC, today) if con is not None else today
    ib = IB()
    ib.RequestTimeout = 90
    try:
        (ib.connect(args.host, args.port, clientId=args.client_id, timeout=15, readonly=True)
         if args.port else tws.connect(ib, client_id=args.client_id, timeout=15))
    except Exception as e:
        print(f"connect fail: {e!r}"); sys.exit(1)

    def ensure_connected(force=False):
        """TWS sessions drop — ROUTINELY on this desk, not exceptionally. Two forms: the API
        socket dies (isConnected() False — the reconnect below fixes it), or the gateway link
        dies while the socket stays up (isConnected() True, every request hangs to its
        timeout — 2026-10-01, 31 requests). Only the breaker sees the second, hence force=True.
        CHECK THE RETURN: False is a fact about the LINK, and letting the caller log it as
        "no bars" for the symbol is how 2.5 hours went up on 2026-10-11.
        """
        if force and ib.isConnected():
            try:
                ib.disconnect()
            except Exception:
                pass
            time.sleep(2)
        for attempt in range(6):
            if ib.isConnected():
                return True
            wait = min(60, 5 * (attempt + 1))
            print(f"  (TWS disconnected — reconnect attempt {attempt + 1}, waiting {wait}s)",
                  flush=True)
            time.sleep(wait)
            try:
                # Reconnect must also be adaptive: the user may have switched TWS
                # between the drop and the retry, which is exactly when a pinned port
                # fails forever.
                (ib.connect(args.host, args.port, clientId=args.client_id, timeout=15,
                            readonly=True)
                 if args.port else tws.connect(ib, client_id=args.client_id, timeout=15))
            except Exception:
                continue
        return ib.isConnected()

    need = duration_days(args.duration)          # how deep we want the series to reach
    SLACK = 20                                   # A rolling window lands a few days short of
                                                 # the nominal duration (weekends, holidays)

    def union_iv(label, cols="max(day), min(day), count(*)"):
        """Read staging UNION the merged file: a staged pass must see what is already in
        market_history, or every pass would re-fetch the entire universe."""
        for tbl in ("iv_history", ):
            try:
                return con.execute(
                    f"""SELECT {cols} FROM (
                        SELECT * FROM mh.{tbl}   WHERE underlying=? AND source=?
                        UNION ALL
                        SELECT * FROM mhro.{tbl} WHERE underlying=? AND source=?)""",
                    [label, SRC, label, SRC]).fetchone()
            except Exception:
                return con.execute(
                    f"SELECT {cols} FROM mh.{tbl} WHERE underlying=? AND source=?",
                    [label, SRC]).fetchone()

    def remembered_oldest(label):
        """The earliest day we have EVER seen for this symbol, from the checks memory.

        Always returns a date or None — never a string. `iv_history_checks` is now shared
        with download_atm_iv.py, which writes oldest_seen as an ISO STRING, so the column's
        declared DATE type no longer describes what is actually in it. Comparing the raw
        value to a date raised "TypeError: '<' not supported between instances of
        'datetime.date' and 'str'" and killed every sweep pass within two minutes. Coerce at
        the single read point rather than guarding each caller.
        """
        raw = None
        try:
            r = con.execute(
                """SELECT min(oldest_seen) FROM (
                       SELECT oldest_seen FROM mh.iv_history_checks
                        WHERE source=? AND underlying=?
                       UNION ALL
                       SELECT oldest_seen FROM mhro.iv_history_checks
                        WHERE source=? AND underlying=?)""",
                [SRC, label, SRC, label]).fetchone()
            raw = r[0] if r else None
        except Exception:                    # target file predates the checks table
            try:
                raw = con.execute("SELECT min(oldest_seen) FROM mh.iv_history_checks "
                                  "WHERE source=? AND underlying=?", [SRC, label]).fetchone()[0]
            except Exception:
                return None
        # Normalise to the ISO STRING form, because that is what the rest of this script
        # compares against: fetch_iv() stores each bar's date via isoformat(), so `oldest`
        # built from `rows` is a string. DuckDB hands back a datetime.date for a DATE column,
        # so the two met as date-vs-str. (First attempt coerced the string to a date — the
        # wrong direction, and it just moved the TypeError one line down.)
        return raw

    def hold_ok(label):
        """True when re-requesting this symbol would tell us nothing new.

        Freshness alone is NOT enough — that was the bug this replaces. A vendor IV request
        returns a rolling window, so a 1-year series looks perfectly "fresh" and would never
        be deepened to the 2 years we asked for.
        """
        newest, oldest, _n = union_iv(label)
        # Coerce both to dates. `iv_history`/`iv_history_checks` now have two writers with
        # different conventions (this script and download_atm_iv.py), so a DATE column can
        # hand back either a datetime.date or an ISO string depending on which file answered.
        # Date arithmetic on the raw value is a TypeError waiting to happen.
        newest, oldest = as_date(newest), as_date(oldest)
        if newest is None or (anchor - newest).days > args.stale_days:
            return False                                   # stale -> refresh regardless
        if oldest is not None and oldest <= today - dt.timedelta(days=need - SLACK):
            return True                                    # as deep as we asked for
        prev = as_date(remembered_oldest(label))
        # Not deep enough — but is this all the vendor HAS? A recent IPO returns the same
        # first day every time. Without this the sweep would re-ask it forever and the
        # supervisor would never terminate.
        return bool(prev and oldest and oldest >= prev - dt.timedelta(days=7))

    n_ok = n_skip = n_fail = 0
    cb = breaker()
    retry_q = []   # (label, contract) that a transient failure cost us this pass
    try:
        for i, (label, contract) in enumerate(items):
            if not ensure_connected():          # the LINK, not the symbol — never "no bars"
                n_fail += 1; cb.failed(label); continue
            if con is not None and hold_ok(label):
                _new, _old, _n = union_iv(label)
                print(f"{label}: skip ({_old} -> {_new}, {_n} rows, meets {args.duration})",
                      flush=True)
                n_skip += 1
                status(_stat, symbol=label, done=i + 1, total=len(items),
                       fetched=n_ok, skipped=n_skip, failed=n_fail)
                continue
            try:
                rows = fetch_iv(ib, label, contract, args.duration)
            except Exception as e:
                print(f"{label}: request fail {e!r}"[:120], flush=True)
                n_fail += 1
                retry_q.append((label, contract))   # always — the symbol that TRIPS the
                                                    # breaker is the most transient of all
                if cb.failed(label):
                    ensure_connected(force=True)
                gate(args.sleep)
                continue
            if not rows:
                print(f"{label}: no bars", flush=True)
                n_fail += 1
                # NEITHER retried NOR counted toward the breaker: "the vendor has nothing"
                # is a fact about the SYMBOL, not the LINK. Retry is for a FLUKY FAILURE --
                # the except branch above, which RAISES; an empty is a result. Five in a row
                # used to force a 120s pause and a TWS disconnect, stalling a healthy sweep.
                # Queueing empties cost ~2 min each every pass: 2026-10-12, 646 retries
                # across two shards recovered ZERO. Safe because ensure_connected() now
                # checks the link BEFORE the fetch, so a dead gateway cannot pose as empty.
                gate(args.sleep)
                continue
            if args.dry_run:
                print(f"{label}: {len(rows)} bars  {rows[0][0]} -> {rows[-1][0]}  "
                      f"last={rows[-1][1]:.2f}")
            else:
                # DELETE + INSERT are ONE logical update, so they go in one transaction.
                # Auto-committed separately, a kill between them (rc=137 is routine) left
                # the symbol with NO rows at all — invisible to the next pass, which saw a
                # symbol that looked unfetched and refetched it, while a kill just after
                # the DELETE of a refresh silently dropped the whole series.
                con.execute("BEGIN TRANSACTION")
                try:
                    con.execute("DELETE FROM mh.iv_history WHERE underlying=? AND source=?",
                                [label, SRC])
                    con.executemany("INSERT INTO mh.iv_history VALUES (?, ?, NULL, ?, NULL, ?)",
                                    [(d, label, v, SRC) for d, v in rows])
                    # Remember how far back this symbol reaches: without it, a name whose full
                    # history is shorter than the requested window is indistinguishable from one
                    # that has not been pulled deep enough, and is re-requested forever.
                    # `rows` carries ISO STRINGS (fetch_iv stores isoformat()), so prev is
                    # normalised to the same form before they meet — mixing the two raised
                    # "TypeError: '<' not supported between 'datetime.date' and 'str'" and
                    # killed every pass within two minutes, all day.
                    prev = remembered_oldest(label)
                    if prev is not None and hasattr(prev, "isoformat"):
                        prev = prev.isoformat()
                    oldest = min(d for d, _ in rows)
                    newest = max(d for d, _ in rows)
                    if prev is not None:
                        oldest = min(oldest, prev)
                    con.execute("""INSERT OR REPLACE INTO mh.iv_history_checks
                                   VALUES (?,?,?,?,?,now())""",
                                [SRC, label, oldest, newest, len(rows)])
                except Exception:
                    con.execute("ROLLBACK")
                    raise
                con.execute("COMMIT")
                print(f"{label}: +{len(rows)} rows ({rows[0][0]} -> {rows[-1][0]}, "
                      f"last={rows[-1][1]:.2f})", flush=True)
            n_ok += 1
            cb.ok()
            status(_stat, symbol=label, done=i + 1, total=len(items),
                   fetched=n_ok, skipped=n_skip, failed=n_fail)
            if i < len(items) - 1:
                gate(args.sleep)

        # Retry what failed, once, at the end of the SAME pass: a symbol that timed out would
        # otherwise wait hours for the next one, and the cause is usually a link that recovers
        # in seconds. Same gate, so a recovery burst cannot push us past the IBKR quota.
        # Anything still failing stays UNRECORDED — failures never reach iv_history_checks, so
        # an empty symbol is never confused with one we gave up on. The retry phase carries
        # the same guards as the main loop: without them a link that died before it started
        # ground the whole queue at ~10.5 s a symbol and recorded each as "still empty".
        if retry_q and con is not None:      # --dry-run has no connection to write to
            print(f"--- retrying {len(retry_q)} failed symbol(s) ---", flush=True)
            for label, contract in retry_q:
                ensure_connected()
                try:
                    rows = fetch_iv(ib, label, contract, args.duration)
                except Exception:
                    rows = None      # None = the REQUEST broke; [] = the symbol has nothing
                if rows:
                    # Same one-transaction rule as the main loop: a kill between the DELETE
                    # and the INSERT must not drop the symbol's rows.
                    con.execute("BEGIN TRANSACTION")
                    try:
                        con.execute("DELETE FROM mh.iv_history WHERE underlying=? AND source=?",
                                    [label, SRC])
                        con.executemany(
                            "INSERT INTO mh.iv_history VALUES (?, ?, NULL, ?, NULL, ?)",
                            [(d, label, v, SRC) for d, v in rows])
                        prev = remembered_oldest(label)
                        _p = prev.isoformat() if hasattr(prev, "isoformat") else prev
                        oldest = min(min(d for d, _ in rows), _p) if _p else \
                            min(d for d, _ in rows)
                        con.execute("""INSERT OR REPLACE INTO mh.iv_history_checks
                                       VALUES (?,?,?,?,?,now())""",
                                    [SRC, label, oldest, max(d for d, _ in rows), len(rows)])
                    except Exception:
                        con.execute("ROLLBACK")
                        raise
                    con.execute("COMMIT")
                    n_ok += 1
                    n_fail -= 1
                    cb.ok()
                    print(f"{label}: recovered on retry (+{len(rows)} rows)", flush=True)
                else:
                    print(f"{label}: still empty after retry", flush=True)
                    if rows is None and cb.failed(label):   # empties are not link evidence --
                        ensure_connected(force=True)        # see the main loop's no-bars note
                gate(args.sleep)
    finally:
        ib.disconnect()
        if con is not None:
            con.close()
    print(f"done: {n_ok} fetched, {n_skip} skipped, {n_fail} failed")


if __name__ == "__main__":
    main()
