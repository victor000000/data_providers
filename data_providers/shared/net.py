"""net.py — retry, and the vendor pacing every request passes through.

The rate budget itself lives in ratelimit.py and the progress files in progress.py; both are
re-exported here, so the 26 modules doing `from data_providers.shared.net import throttle, status` keep
working and there is ONE definition of each instead of a copy that drifts.

Assumptions, from experience on this box (2026-09-30):
  * the network is unstable — any single request may fail, so every call retries with
    exponential backoff before it is allowed to fail a ticker;
  * a run can die at any moment — so progress is written to `var/status/<name>.json` after
    every item, and `cat` on it answers "where is it now" without reading a log;
  * one bad ticker must never kill the run — the item loop catches, records and moves on.

    from data_providers.shared.net import retry, status, throttle
    bars = retry(lambda: fetch(url), what="META 2024-02-16")
    status("download_option_bars", ticker="META", done=3, total=8)
"""
import pathlib
import sys
import time

from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.shared import progress, ratelimit
from data_providers.shared.progress import status                       # noqa: F401 — re-export


def retry(fn, tries=4, base=1.5, what=""):
    """Call fn(), retrying transient failures with exponential backoff."""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:                     # noqa: BLE001 — the point is to catch anything
            last = e
            if i < tries - 1:
                time.sleep(base ** i)
    raise RuntimeError(f"{what or 'request'} failed after {tries} tries: {last!r}")


def throttle(source, margin=0.95):
    """Block long enough that **every process on this box together** stays under `source`'s cap.

    margin < 1 leaves headroom for retries and for other scripts sharing the key. Call it
    immediately before every request; it is cheap and idempotent. The mechanism — a file per
    source, flocked, holding the next allowed slot — is in ratelimit.py, with the account of
    why in-process state alone was not enough.
    """
    # Module-qualified, NOT `from ... import claim_slot`: the bound name is frozen at import,
    # so patching ratelimit.claim_slot in a test would not reach this call -- the test would
    # then spend real seconds on the real gate and still pass its assertion.
    limit, window = ratelimit.LIMITS[source]
    interval = window / (limit * margin)
    with ratelimit._throttle_lock:     # keep threads off the file lock; one claim per call
        wait = ratelimit.claim_slot(source, interval)
    if wait > 0:
        time.sleep(wait)


def note_headers(source, headers):
    """Feed a response's rate-limit headers back in (Alpaca only, for now).

    If the API says the window is nearly exhausted, the next throttle() waits for that
    window's reset. Belt and braces: the fixed interval keeps us polite, the header keeps us
    correct when another process shares the key.
    """
    if source != "alpaca":
        return
    try:
        remaining = int(headers.get("X-Ratelimit-Remaining", "999"))
        reset = float(headers.get("X-Ratelimit-Reset", "0"))
        if remaining <= 5 and reset > 0:
            wait = reset - time.time()
            if 0 < wait < 90:
                time.sleep(wait)
    except (TypeError, ValueError):
        pass
