import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""fetch_cn_products.py — drive CN option fetches from cn_product_map, not hard-coded lists.

The old fetcher took --czce-product / --ine-days / --cffex-contracts, so covering the universe
meant editing the script per venue. cn_product_map already knows which endpoint and which code
format each product uses, so this walks the map instead: adding a product is a row, not a patch.

Written into the new CN schema (venue/product first-class, exchange IV and delta stored), so
the 16 previously-unreachable products land in the right shape the first time.

    uv run python apps/data_ops/fetch_cn_products.py \
        [--venues SHFE,DCE,GFEX,CFFEX] [--limit N]
"""
import argparse
import datetime as dt
import pathlib
import sys
import time

import duckdb

from data_providers.data import clock  # noqa: E402
from data_providers.jobs.data_ops.cn_expiry import expiry_for  # noqa: E402,F401
from concurrent.futures import ThreadPoolExecutor, as_completed   # noqa: E402
from data_providers.shared.net import retry, throttle                                   # noqa: E402

ROOT = bootstrap.ROOT
CN = ROOT / "cn_market.duckdb"
# How recent a CN product's newest bar must be before the product counts as covered.
# Generous on purpose: CN holidays (Spring Festival, Golden Week) run for a week or more,
# and a tight window would re-fetch every product after each one.
# NOTE: this value is only correct for the CALENDAR fallback. When the data anchor is
# available the tolerance must be tight — see CN_LAG_TOLERANCE_DAYS.
CN_CURRENT_DAYS = 10
# How far behind the SESSION ANCHOR a product may fall and still count as covered.
# Deliberately ~0, and that is the whole point of anchoring to data: the anchor stops
# advancing during a holiday, so it already absorbs the holiday. A generous tolerance on top
# of it re-introduces the bug it was meant to remove — with the anchor at 2026-10-09 and a
# product at 2026-09-30 the lag is 9 days, which `<= 10` would SKIP even though Oct 8-9 exist
# and are missing. One day of slack only, to avoid churn on a product that legitimately has
# no print on the newest session.
CN_LAG_TOLERANCE_DAYS = 1


def num(v):
    """CN frames use EMPTY STRINGS for missing numbers, not nulls. float('') raises, and with
    the old bare `except: continue` that silently dropped 557 of CU's rows. Empty,
    whitespace and unparseable all mean "no value" -- never 0."""
    if v is None:
        return None
    t = str(v).strip().replace(",", "")
    if not t or t in ("-", "--"):
        return None
    try:
        f = float(t)
    except ValueError:
        return None
    # NaN and inf are not values. `str(nan) == 'nan'` and `float('nan')` SUCCEEDS, so the
    # obvious guard misses it: a pandas NaN from a JSON/HTML-sourced DCE/GFEX frame would be
    # stored as NaN, which DuckDB treats as NOT NULL -- it sorts above every number, poisons
    # avg(), and passes `CHECK (close >= 0)`. f == f is the NaN test; inf is caught by range.
    return f if f == f and abs(f) != float("inf") else None


def parse_code(code, fmt, venue=None, cal=None):
    """CZCE 'MA611' is YMM; SHFE/DCE 'sc2612' is YYMM; GFEX writes 'si2611-C-10000'.

    The format comes from the MAP, so a new venue cannot silently inherit the wrong
    convention -- but the SEPARATORS do not, and that cost every GFEX row. The old parser read
    the character right after the month as the call/put flag, so on 'si2611-C-10000' it read
    '-' and returned None: a GFEX bars fetch stored ZERO rows while reporting success, because
    a None here is indistinguishable from a malformed contract. Measured 2026-10-07 on all
    1611 rows of lc2611: every one dropped.
    """
    s = str(code).strip()
    i = 0
    while i < len(s) and not s[i].isdigit():
        i += 1
    root, rest = s[:i].upper(), s[i:]
    j = 0
    while j < len(rest) and rest[j].isdigit():
        j += 1
    ymd = rest[:j]
    tail = rest[j:].lstrip("-_. ")          # GFEX separates: si2611-C-10000
    cp = tail[:1].upper()
    strike = tail[1:].lstrip("-_. ")
    if not ymd or cp not in ("C", "P"):
        return None
    if fmt == "YMM" and len(ymd) == 3:
        yy, mm = int(ymd[0]), int(ymd[1:])
        year = (clock.today().year // 10) * 10 + yy
        if year < clock.today().year - 5:
            year += 10
    elif len(ymd) == 4:
        yy, mm = int(ymd[:2]), int(ymd[2:])
        year = 2000 + yy
    else:
        return None
    if not 1 <= mm <= 12:
        return None
    try:
        exp = expiry_for(venue, year, mm, cal)
        k = float(strike)
    except ValueError:
        return None
    return root, exp, cp, k


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venues", default="SHFE,DCE,GFEX,CFFEX")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6,
                    help="concurrent day requests (akshare gate is 60/30s)")
    ap.add_argument("--days", type=int, default=60, help="SHFE: lookback days (one call each)")
    ap.add_argument("--products", nargs="*", default=None,
                    help="restrict to these product codes; the filter that lets a --force "
                         "run resume without re-fetching the products already filled")
    ap.add_argument("--force", action="store_true",
                    help="re-fetch even when the product's newest bar is current — needed to "
                         "DEEPEN a series, since recency is what the skip test measures")
    args = ap.parse_args()
    want = {v.strip().upper() for v in args.venues.split(",") if v.strip()}

    import akshare as ak
    import warnings
    warnings.filterwarnings("ignore")
    # The exchange calendar, so a contract's stored expiry counts TRADING days: October 2026
    # opens on the 8th, so m2611 expires on the 23rd, not the 16th a weekday count gives.
    cal = sorted(d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])
                 for d in ak.tool_trade_date_hist_sina()["trade_date"])

    con = duckdb.connect(str(CN))
    rows = con.execute("""SELECT canonical, venue, product, ak_symbol, endpoint, code_format
                          FROM cn_product_map ORDER BY venue, product""").fetchall()
    todo = [r for r in rows if r[1] in want]
    # A PRODUCT FILTER IS WHAT MAKES THIS RESUMABLE. The skip below is a RECENCY test, and
    # during a market holiday every series looks stale -- 2026-09-30 against a clock 7 days
    # on, inside the 10-day window only by luck. So --force is needed to fill anything at
    # all, and --force then ignores the skip for EVERY product: resuming after a stop
    # re-fetched AL and AU, ~3h of work already committed, for identical values. Naming the
    # products that still need filling is the only way to resume without re-buying the rest.
    if args.products:
        keep_ = {p.upper() for p in args.products}
        todo = [r for r in todo if r[2].upper() in keep_]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} products across {sorted({r[1] for r in todo})}", flush=True)

    # ── ANCHOR THE RECENCY TEST TO THE DATA, NOT THE CALENDAR ────────────────────────────
    # The skip below used to compare a product's newest bar against `clock.today()`. Across a
    # holiday that comparison is nonsense: nothing trades, so every series ages while no new
    # data exists, and the same age means "stale, refetch" during the holiday and "stale,
    # refetch" forever after it. Measured 2026-10-10: SHFE sat at 2026-09-30 against a clock
    # 10 days on — age exactly CN_CURRENT_DAYS — so `<=` matched and CU/RU/AU/AL/ZN were
    # SKIPPED, silently leaving the 8-9 Oct sessions unfilled. China's National Day holiday
    # reproduces that exact 10-day gap every year, so it was a recurring skip, not a one-off.
    #
    # Comparing against the newest day held ANYWHERE in option_bars fixes it at the root: in
    # a holiday that value stops advancing too, so products stay "current" and nothing is
    # re-bought; the moment any venue fetches a new session it advances, laggards fall behind
    # and are refetched. Same lesson as download_atm_iv.py's FULL_BAND_DAYS.
    try:
        _market_newest = con.execute("SELECT max(day) FROM option_bars").fetchone()[0]
    except Exception as e:
        print(f"  !! market-newest read failed ({repr(e)[:60]}) — falling back to today's "
              f"date, which is what caused the holiday skip", flush=True)
        _market_newest = None

    ok = fail = 0
    for canon, venue, prod, ak_sym, endpoint, fmt in todo:
        # `have > 0` was a ONE-SHOT lock: any row at all skipped the product forever. So a
        # first run that was partial -- a day lost to a timeout, or the default 60-day
        # window on a venue whose history goes back further -- could never be repaired, and
        # the missing days were invisible because the product simply reported "skipped" on
        # every later run. Coverage is now judged by RECENCY: skip only when the product's
        # newest row is current, so a stale or short series is re-fetched.
        have, newest = con.execute("""SELECT count(*), max(day) FROM option_bars
                                      WHERE venue=? AND product=?""",
                                   [venue, prod]).fetchone()
        # Data-anchored: lag is measured against the newest session held anywhere, not the
        # wall clock — and with a TIGHT tolerance, because the anchor already absorbs the
        # holiday. See the notes above the loop and at CN_LAG_TOLERANCE_DAYS.
        _ref = _market_newest or clock.today()
        _tol = CN_LAG_TOLERANCE_DAYS if _market_newest is not None else CN_CURRENT_DAYS
        if (have and newest is not None and (_ref - newest).days <= _tol
                and not args.force):
            print(f"  {venue}:{prod:<5} already {have:,} rows, newest {newest} — skipped",
                  flush=True)
            continue
        if have:
            print(f"  {venue}:{prod:<5} {have:,} rows but newest is {newest} — RE-FETCHING",
                  flush=True)
        fn = getattr(ak, endpoint, None)
        if fn is None:
            print(f"  {venue}:{prod:<5} no akshare endpoint {endpoint}", flush=True)
            fail += 1
            continue
        try:
            if endpoint in ("option_hist_shfe", "option_hist_dce",
                             "option_hist_gfex"):
                # SHFE, DCE and GFEX all return a SINGLE DAY and carry NO date column -- the
                # design doc predicted this ("date column: absent (implied by the request)").
                # With no date the first version skipped every row and reported "+0 rows" as
                # though the product had no data. History therefore comes from trade_date,
                # one request per day. DCE/GFEX were called with symbol alone, which is why
                # they raised KeyError on every attempt and looked vendor-blocked.
                # PARALLEL across days. This loop was serial with no rate gate at all, so
                # throughput was 1/(latency+sleep) and akshare's 60/30s budget -- the
                # highest ceiling of any source we use -- went almost entirely unused. Days
                # are independent, so workers fetch concurrently while every request still
                # claims a slot through throttle("akshare"): parallel WITHOUT bypassing the
                # budget, which is the distinction that matters.
                _days = [clock.today() - dt.timedelta(days=b) for b in range(args.days)]
                _days = [d for d in _days if d.weekday() < 5]

                def _one_day(d):
                    throttle("akshare")
                    f = retry(lambda: fn(symbol=ak_sym, trade_date=d.isoformat()),
                              tries=3, what=f"{ak_sym} {d}")
                    if f is not None and len(f):
                        f = f.copy()
                        f["_day"] = d
                        return d, f, None
                    return d, None, None

                frames, day_err = [], []
                with ThreadPoolExecutor(max_workers=args.workers) as _ex:
                    for _fut in as_completed({_ex.submit(_one_day, d): d for d in _days}):
                        try:
                            d, f, _ = _fut.result()
                        except Exception as e:
                            # Count, don't swallow: a day lost to a timeout produced no
                            # frame, so the series came back silently SHORT and looked
                            # complete -- the same failure the num() fix closes one level down.
                            day_err.append(("?", type(e).__name__))
                            continue
                        if f is not None:
                            frames.append(f)
                    time.sleep(0.4)
                if day_err:
                    print(f"  {venue}:{prod:<5} {len(day_err)} day(s) FAILED "
                          f"(e.g. {day_err[0][0]} {day_err[0][1]}) — series is SHORT",
                          flush=True)
                df = frames[0] if len(frames) == 1 else (
                    __import__("pandas").concat(frames, ignore_index=True) if frames else None)
                if df is None:
                    print(f"  {venue}:{prod:<5} no frames for {args.days}d", flush=True)
                    fail += 1
                    continue
            else:
                df = fn(symbol=ak_sym)
        except Exception as e:
            print(f"  {venue}:{prod:<5} FETCH FAILED {type(e).__name__}: {str(e)[:52]}",
                  flush=True)
            fail += 1
            time.sleep(1)
            continue

        # Column names differ per venue and are carried in the map; fall back to the
        # SHFE shape, which is the one verified live.
        cols = {c: c for c in df.columns}
        code_col = next((cols[c] for c in df.columns if "合约" in c or "代码" in c), None)
        date_col = next((cols[c] for c in df.columns if "日期" in c or "交易日" in c), None)
        if date_col is None and "_day" in df.columns:
            date_col = "_day"      # set by the per-day loop above
        o = next((cols[c] for c in df.columns if c in ("开盘价", "今开盘")), None)
        cl = next((cols[c] for c in df.columns if c in ("收盘价", "今收盘")), None)
        vol = next((cols[c] for c in df.columns if "成交量" in c), None)
        oi = next((cols[c] for c in df.columns if "持仓量" in c), None)
        stl = next((cols[c] for c in df.columns if c in ("结算价", "今结算")), None)
        iv = next((cols[c] for c in df.columns if "隐含波动率" in c), None)
        delta = next((cols[c] for c in df.columns
                      if "delta" in c.lower() or "德尔塔" in c), None)
        if not (code_col and o and cl):
            print(f"  {venue}:{prod:<5} unrecognised frame cols={list(df.columns)[:6]}",
                  flush=True)
            fail += 1
            continue

        n = 0
        n_err = 0
        for _, r in df.iterrows():
            p = parse_code(r[code_col], fmt, venue, cal)
            if not p:
                continue
            root, exp, cp, k = p
            day = r[date_col] if date_col else None
            if day is None:
                continue
            d = day if isinstance(day, dt.date) else dt.date.fromisoformat(str(day)[:10])
            try:
                con.execute("""INSERT INTO option_bars
                    (venue,product,contract,expiry,strike,call_put,day,open,close,volume,oi,iv,delta,settle,source)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT DO UPDATE SET
                        settle=COALESCE(EXCLUDED.settle, option_bars.settle),
                        oi=COALESCE(EXCLUDED.oi, option_bars.oi),
                        iv=COALESCE(EXCLUDED.iv, option_bars.iv),
                        delta=COALESCE(EXCLUDED.delta, option_bars.delta)""",
                    [venue, prod, str(r[code_col]), exp, k, cp, d,
                     num(r[o]),
                     num(r[cl]),
                     num(r[vol]) if vol else None,
                     num(r[oi]) if oi else None,
                     num(r[iv]) if iv else None,
                     num(r[delta]) if delta else None,
                     # 结算价 is the exchange's SETTLEMENT, which is what CN margins and
                     # marks against -- not the same number as the close, and dropped here
                     # until 2026-10-07 (0% filled on every venue).
                     num(r[stl]) if stl else None,
                     f"akshare_{venue.lower()}_{prod.lower()}"])
                n += 1
            except Exception as e:
                # Count and REPORT failures. A bare `continue` here is what made this
                # script print "+0 rows" for five products: every INSERT was failing on a
                # column-name mismatch (the table had `symbol`, the INSERT named
                # `contract`) and the exception was swallowed, so a schema error looked
                # exactly like an empty product.
                n_err += 1
                if n_err == 1:
                    print(f"  {venue}:{prod:<5} first insert error: {repr(e)[:90]}", flush=True)
                continue
        if n_err:
            print(f"  {venue}:{prod:<5} {n_err:,} rows FAILED to insert (see above)", flush=True)
        print(f"  {venue}:{prod:<5} +{n:,} rows", flush=True)
        ok += 1
        time.sleep(2)
    con.close()
    print(f"done: {ok} products fetched, {fail} failed")


if __name__ == "__main__":
    main()
