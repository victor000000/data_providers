import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""tws_watch_and_sweep.py — launch the IV sweep the moment TWS's historical feed returns.

WHY THIS EXISTS

On 2026-10-02 the HMDS historical farm (`ushmds`) went down while everything else looked
healthy: the socket connected, `managedAccounts()` returned the live account, and the live
quote farms all reported OK. Every `reqHistoricalData` returned 0 bars.

That blocked the one download that matters -- the 2-year IV history for the screened
universe -- and it cannot be fixed from here: only a TWS/IB Gateway restart re-establishes
the farm. So the block is external, but the LATENCY after it clears is not. Without this
watcher the sweep waits for someone to notice, re-probe by hand, and remember the exact
command. With it, the sweep starts within a couple of minutes of the feed coming back.

HOW IT DECIDES

It does not parse farm-status strings. It asks the functional question directly: does a
5-day daily-bar request for a liquid name return any bars at all? That is the same call the
sweep itself makes, so a pass here means the sweep will actually work -- no inference from
a status line that could be stale, and no dependence on which error codes IBKR emits today.

Stops after MAX_HOURS so a feed that never returns cannot leave a poller running forever.

    uv run python apps/data_ops/tws_watch_and_sweep.py \
        [--universe plans/ibkr_iv_universe_scoped.txt] [--duration "2 Y"]
"""
import argparse
import pathlib
import subprocess
import sys
import time

from data_providers.data import clock  # noqa: E402

ROOT = bootstrap.ROOT
POLL_SECONDS = 120
MAX_HOURS = 12


def feed_is_up():
    """True when a real historical request returns real bars."""
    from ib_async import IB, Stock
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    ib = IB()
    try:
        # Was its own ib.connect("host.docker.internal", port, ...) with 4001 passed in
        # from a --port default. This script knew about both setups while thirteen others
        # did not -- the knowledge was local, so it did not help them. Now it uses the
        # shared connect() and the knowledge lives in one place.
        from data_providers.providers import tws as _tws
        _tws.connect(ib, client_id=977, timeout=15)
        c = Stock("AAPL", "SMART", "USD")
        ib.qualifyContracts(c)
        bars = ib.reqHistoricalData(c, "", "5 D", "1 day", "TRADES", True, 1)
        return len(bars) > 0
    except Exception:
        return False
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="plans/ibkr_iv_universe_scoped.txt")
    ap.add_argument("--duration", default="2 Y")
    # TWS uses 7496/7497; IB GATEWAY uses 4001/4002. The gateway was restarted onto
    # 4001 on 2026-10-02 and 7496 stopped listening, so a hard-coded 7496 would have
    # polled a dead port forever and never launched the sweep.
    # None, not 4001. The default was the assumption this script existed to work around
    # -- its own comment records the gateway moving to 4001 on 2026-10-02 and 7496
    # stopping -- and then it baked 4001 back in as the default. With no --port we now
    # ask tws.connect() which one is up.
    ap.add_argument("--port", type=int, default=None,
                    help="pin a port; omit to detect Gateway (4001/4002) or desktop (7496/7497)")
    args = ap.parse_args()

    deadline = time.time() + MAX_HOURS * 3600
    print(f"[{clock.now()}] watching TWS :{args.port or 'auto'} — polling every {POLL_SECONDS}s, "
          f"giving up after {MAX_HOURS}h", flush=True)

    while time.time() < deadline:
        if feed_is_up():
            print(f"[{clock.now()}] FEED IS UP — launching the IV sweep", flush=True)
            # The sweep is idempotent (it skips symbols whose newest row is fresh), so
            # launching it here cannot duplicate anything already fetched.
            return subprocess.run(
                [sys.executable, "-m", "data_providers.jobs.history.fetch_iv_history_ibkr",
                 "--universe", args.universe, "--duration", args.duration],
                cwd=str(ROOT)).returncode
        print(f"[{clock.now()}] feed still down (0 bars for AAPL) — waiting", flush=True)
        time.sleep(POLL_SECONDS)

    print(f"[{clock.now()}] gave up after {MAX_HOURS}h; feed never returned. "
          f"Restart TWS/IB Gateway and re-run this watcher.", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
