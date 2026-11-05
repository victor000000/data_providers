"""shard_control.py — the health signals and the scaling decision they feed.

Split out of adaptive_sweep.py for the 100-line cap. `decide` is deliberately PURE — numbers
in, an action out, no process and no filesystem — which is what makes it testable.

THE ERROR REGEX. `Error 162` carries TWO unrelated meanings and only one is a problem:

    "... query cancelled"    -> real pacing. IBKR refused us; back off.
    "... returned no data"   -> the contract has no IV history. NORMAL across a 6,000-symbol
                                universe, and worth nothing as a health signal.

Counting both made a healthy run read 14-15% "error ratio" — past the scale-down threshold — so
the controller would have throttled a healthy run because many tickers simply have no options.
Measured 2026-10-10: 24 genuine pacing vs 98 no-data. The old regex counted 139; this one 27.
Error 200 ("no security definition", e.g. a delisted ticker) is excluded for the same reason:
it is a fact about the symbol, not about the link.

The downloader's OWN breaker message ("the IBKR link is probably down") IS counted, by that
same rule: the clearest LINK fact in the logs, and without it a dead gateway reads `errors=0`.

THE CONTROL LAW, two conditions rather than one:
  * error ratio above HIGH        -> scale DOWN. The vendor is refusing work; asking for more
                                     of it is the wrong response.
  * ratio below LOW AND the last scale-up actually BOUGHT throughput (>= GAIN) -> scale UP.
  * otherwise                     -> SATURATE and hold.

The gain condition exists because the error ratio alone is not evidence that more shards help.
Measured 2026-10-10: IBKR delivered 24 fetches/min at 1, 2, 3 AND 4 shards — the gateway
serialises historical requests per account — so a controller watching only the error ratio
scaled to max, reported success, and changed nothing.
"""
import re
import time

UP, DOWN, HOLD = "up", "down", "hold"

DONE_RE = re.compile(r": (\+[0-9]+ rows|skip |no bars|still empty after retry)")
ERR_RE = re.compile(r"Error 162[^\n]*query cancelled|Error (1102|1100)|: request fail|"
                    r"Traceback|link is probably down|API connection failed")


def say(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def counts(log):
    """(processed, errors, no_bars) since the shard's LAST start, read from its log.

    Since the last `=== shard N start` marker, NOT the whole file. The log is append-mode, so
    counting all of it credits a previous run's matches to this one: measured 2026-10-10 as
    `processed=7253 errors=70 ratio=1.0%` on the very first interval, which reads as headroom
    and fires a spurious scale-up. It would also double-count a shard the controller restarted.
    """
    try:
        txt = log.read_text(errors="replace")
    except Exception:
        return 0, 0, 0
    run = txt[txt.rfind("=== shard "):]     # -1 on a log with no marker: one char, counts 0
    return len(DONE_RE.findall(run)), len(ERR_RE.findall(run)), run.count(": no bars")


def progress(idle, denom, limit):
    """-> (idle intervals so far, whether to stop). Says the warning itself, once per `limit`.

    A shard hung on a refused socket is ALIVE by every other check the sweep makes. Measured
    2026-10-10: TWS died at 13:17 and two shards retried for SIX HOURS while the supervisor
    reported `processed=0 errors=0 ratio=0.0%` -- and 0.0% reads as HEADROOM, so a dead sweep
    looked healthy. Nothing else gives up either: the downloader's breaker resets its counter by
    design. Zero progress from EVERY shard is its own signal. Warn at `limit`, stop at 6x -- an
    hour at the default: long enough to ride out a gateway restart, bounded against a dead one.
    """
    idle = idle + 1 if not denom else 0
    if idle and not idle % limit:
        say(f"  STALLED {idle} intervals — no shard is progressing; the source is down, "
            f"not slow")
        if idle >= limit * 6:
            return idle, True
    return idle, False


def decide(*, n_running, n_started, n_total, ratio, d_done, rate_before, saturated,
           low, high, gain, min_shards, max_shards):
    """-> (action, reason, rate_before, saturated).

    `rate_before` is the throughput measured just before the previous scale-up; `None` means no
    scale-up has happened yet, so there is nothing to judge. A scale-DOWN resets both, since the
    baseline it was comparing against is no longer meaningful.
    """
    if ratio > high and n_running > min_shards:
        return DOWN, f"ratio {ratio:.1%} > {high:.0%}", None, False

    if ratio <= low and n_running < max_shards and not saturated:
        if rate_before is not None:
            got = (d_done - rate_before) / rate_before if rate_before else 0.0
            if got < gain:
                return HOLD, (f"last scale-up gained {got:+.0%} (< {gain:.0%}) — SATURATED at "
                              f"{n_running} shards"), rate_before, True
        if n_started < n_total:
            return UP, f"ratio {ratio:.1%} <= {low:.0%} (baseline {d_done}/interval)", d_done, False
    return HOLD, "", rate_before, saturated
