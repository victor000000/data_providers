"""ratelimit.py — the shared request budget, and the file that makes it shared.

Split out of net.py, which had reached the 100-line cap. The budget is shared across
PROCESSES, not just threads: in-process state alone was a real bug — a module-level
`_last_call` lives in one process's memory, so three concurrent Alpaca jobs each believed they
owned the full 200/min and the box ran at ~3x the cap (2026-10-01). Hence a FILE per source
holding the next allowed slot, read and advanced under an flock.
"""
import os
import pathlib
import sys
import threading
import time

from data_providers import runtime as bootstrap  # noqa: F401

ROOT = bootstrap.ROOT

# Every source we pull from has a published (or de-facto) cap. A script that "just sleeps a
# bit" drifts over them quietly, and the penalty is not a 429: IBKR pauses ALL historical data
# for ~10 minutes, Yahoo throttles an IP, Futu disconnects the OpenD session.
#   alpaca    200 requests / 60 s   (data API; the docs' hard number)
#   ibkr       60 historical / 600 s (pacing rule; ~6 msg/s otherwise)
#   futu       30 requests / 30 s   (per quote category, OpenD)
#   akshare    60 requests / 30 s   (self-imposed — it hits exchange sites directly)
#   yfinance   60 requests / 60 s   (self-imposed — Yahoo throttles abusers)
LIMITS = {
    "massive": (5, 60),
    "alpaca": (200, 60),
    "ibkr": (60, 600),
    "futu": (30, 30),
    # akshare is SELF-imposed (it scrapes exchange sites): 60/30s is 2 req/s, 3x the 20/30s it
    # replaced. Raise the WORKERS too, or a low cap just makes the extra threads wait.
    "akshare": (60, 30),
    "yfinance": (60, 60),
}
# A bounded backfill raises one source's cap for its own command: `AKSHARE_LIMIT=120/30 <cmd>`,
# `IBKR_LIMIT=3600/600 <cmd>`. The gate is shared across processes, so it raises the rate for
# EVERY job then running — hence per-command, never a new default. (IBKR's raise bought 4.8x
# but cost 46 Error 162s, so its default stays conservative.)
for _src, _var in (("akshare", "AKSHARE_LIMIT"), ("ibkr", "IBKR_LIMIT")):
    _override = os.environ.get(_var)
    if _override:
        _n, _w = (int(x) for x in _override.split("/"))
        LIMITS[_src] = (_n, _w)

GATE_DIR = ROOT / "var" / "ratelimit"
_last_call = {}
_gate_warned = set()   # sources whose cross-process gate fell back, so we warn once
_throttle_lock = threading.Lock()      # several worker threads share one budget


def claim_slot(source, interval):
    """Reserve the next slot in `source`'s shared gate. -> seconds to wait before sending.

    Falls back to in-process pacing if the gate file cannot be used (read-only checkout, full
    disk): a slightly impolite run beats a crashed one."""
    import fcntl
    now = time.time()
    try:
        GATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(GATE_DIR / f"{source}.gate", "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                nxt = float(f.read().strip() or 0.0)
            except ValueError:         # a truncated write mid-crash: treat as free
                nxt = 0.0
            # CLAMP. The value is trusted otherwise, and flock only protects against a dead
            # PROCESS, not a bad VALUE: a clock step, a manual date change, or any
            # parseable-but-absurd number leaves a far-future slot and every process on the
            # box then sleeps until it. Any legitimate value is within a few intervals of
            # now, so anything past that is corruption.
            horizon = now + interval * 100
            if nxt > horizon:
                print(f"  !! rate-limit gate '{source}' held a slot {nxt - now:.0f}s out "
                      f"({nxt - horizon:.0f}s beyond any plausible value) — resetting it. "
                      f"A clock step or a corrupt gate file can wedge every process here.")
                nxt = 0.0
            slot = max(now, nxt)
            f.seek(0)
            f.truncate()
            f.write(f"{slot + interval:.6f}")
            f.flush()
            fcntl.flock(f, fcntl.LOCK_UN)
        return max(0.0, slot - now)
    except OSError as e:
        # SAY SO. Falling back quietly made this per-PROCESS pacing, so N concurrent jobs
        # each believed it owned the full allowance and the aggregate rate was N times the
        # limit -- the exact throughput bug the gate exists to prevent. Reported once per
        # source so a read-only checkout or a full disk is visible rather than silently
        # degrading into over-requesting.
        if source not in _gate_warned:
            _gate_warned.add(source)
            print(f"  !! rate-limit gate for '{source}' is unusable ({repr(e)[:60]}); "
                  f"falling back to per-PROCESS pacing. Concurrent jobs will exceed the "
                  f"limit by the number of jobs -- this is not equivalent.")
        wait = _last_call.get(source, 0.0) + interval - time.monotonic()
        _last_call[source] = max(time.monotonic(), _last_call.get(source, 0.0) + interval)
        return max(0.0, wait)
