"""venue.py — the day-walk for the CN venues that PUBLISH their own ATM IV.

One request per product-day, driven from cn_product_map so a new product is a row and not a
patch. Days already stored are skipped, so a re-run costs one query instead of 400 requests.

GIVING UP IS PART OF THE LOOP. On 2026-10-07 GFEX answered every request for the first ~20
days of LC and then refused all of them -- same symbols, same dates that had worked minutes
earlier, JSONDecodeError every time. That is a rate limit tripped by 8 concurrent workers,
not 404 special days, and the first version of this spent 404 failed requests (each retried
3 times) learning nothing after the first twenty. A chunk that is nearly all failures means
the source is refusing US: stop, keep what is stored, and resume there next run.
"""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

from data_providers.jobs.history.cn_iv.frames import day_from_contract_hist, day_from_series_vol
from data_providers.shared.net import retry, status, throttle

# Venue -> the endpoint carrying that venue's own IV. DCE is deliberately absent: its
# endpoint answers HTTP 412 with a JavaScript challenge (akshare reports JSONDecodeError),
# so DCE is built by the solver path instead -- see product.py.
#
# GFEX is absent for the same reason, and it was here for weeks: `option_hist_gfex` answers
# the first ~20 requests of LC and SI and then refuses all of them, so being listed bought
# nothing and cost a full run of wasted requests -- option_bars holds 105,872 LC/SI rows and
# EVERY one came from sina_lc_bars/sina_si_bars. There is no pace at which GFEX offers bulk
# history, so "re-run later and with fewer --workers" was advice that could never come true.
VENUE_ENDPOINT = {
    "SHFE": "option_vol_shfe",
    "INE": "option_vol_shfe",     # INE's 原油期权 is served by the SHFE endpoint
}
SOURCE_SUFFIX = {"SHFE": "vol", "INE": "vol"}
CHUNK_DAYS = 40
ABORT_AT = 8


def weekdays(start, end):
    """Every weekday in range. CN holidays are NOT excluded -- the venues answer them with an
    empty frame, which is a skip rather than an error."""
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def fetch_days(fn, ak_sym, days, point_fn, workers, on_progress=None):
    """-> (points, failed, unfetched). Fetches `days` in chunks, aborting if refused."""
    def one_day(d):
        throttle("akshare")
        f = retry(lambda: fn(symbol=ak_sym, trade_date=d.isoformat()),
                  tries=3, what=f"{ak_sym} {d}")
        if f is None or not len(f):
            return d, None            # holiday or weekend: no rows, not an error
        return d, point_fn(f)

    points, failed, unfetched = {}, [], 0
    for i in range(0, len(days), CHUNK_DAYS):
        chunk = days[i:i + CHUNK_DAYS]
        chunk_failed = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(one_day, d): d for d in chunk}
            for fut in as_completed(futures):
                try:
                    d, iv = fut.result()
                except Exception as e:                          # noqa: BLE001
                    failed.append((futures[fut], type(e).__name__))
                    chunk_failed += 1
                    continue
                if iv is not None:
                    points[d] = iv
        if on_progress:
            on_progress(points, failed, len(days))
        if chunk_failed >= ABORT_AT and chunk_failed > 0.6 * len(chunk):
            unfetched = len(days) - i - len(chunk)
            break
    return points, failed, unfetched


def report(venue, prod, points, failed, unfetched):
    """The one-line verdict per product, loud about anything short."""
    if unfetched:
        return (f"  {venue}:{prod:<4} REFUSED — {len(failed)} request(s) failed and the last "
                f"{CHUNK_DAYS} were almost all failures; stopped with {unfetched} day(s) "
                f"unfetched. Re-run later (it resumes) and with fewer --workers.")
    # Count, don't swallow: a day lost to a timeout leaves the series SHORT, and a short
    # series ranks its latest IV against fewer points without saying so.
    if failed:
        return (f"  {venue}:{prod:<4} {len(failed)} day(s) FAILED (e.g. {failed[0][1]}) — "
                f"series is SHORT")
    return f"  {venue}:{prod:<4} {len(points):>4} points"


def progress_writer(venue, prod):
    def on_progress(points, failed, total):
        status("build_iv_history_cn_venue", venue=venue, product=prod,
               fetched=len(points) + len(failed), total=total)
    return on_progress
