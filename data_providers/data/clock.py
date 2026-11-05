import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""clock.py — the one definition of "now" in this repo.

WHY THIS EXISTS

Every timestamp column here stores NAIVE UTC (build_positions.py spells the rule out at
its `as_of` column), but the box itself runs Asia/Shanghai, UTC+8. So the obvious
`date.today()` is eight hours ahead of the data it is compared against, and that gap is
not cosmetic:

  * a report written at 22:00 UTC was filed as `..._<tomorrow>.md` while every row inside
    it was stamped with today's UTC date -- the filename and its contents disagreed by a day;
  * `T_years()` computed after 16:00 UTC silently lost a day of time-to-expiry, which
    shifts every greek and IV in the capture;
  * a downloader's "today" rolled over at 08:00 UTC, mid-session for US markets.

None of these announce themselves. The date is still a plausible date, just not the one
the rest of the system means.

THE RULE

Use `clock.now()` / `clock.today()` for anything that will be STORED, COMPARED against a
stored stamp, or used to name an artifact. Local time is still correct where the question
is genuinely "what day is it where the market is" -- ibkr_order_collector.py keeps its
explicit Asia/Shanghai session day for exactly that reason, and the NY->UTC converters in
load_ibkr_flex.py / load_ibkr_activity.py stay as they are.

    uv run --no-project python -c "import sys; sys.path.insert(0,'scripts'); import clock; print(clock.now())"

WHY THIS FILE ALSO SETS THE PROCESS TIMEZONE

The two functions below only fix OUR calls. Everything else in the process still read the
box's zone: a bare `duckdb.connect()` stamped `now()` in Asia/Shanghai (four such writes
shipped before they were caught), shell `date` printed local time, and any library that
resolves the local zone -- pandas, the futu SDK, a subprocess -- would have done the same.

So importing this module also pins the PROCESS to UTC, which makes naive `datetime.now()`
return UTC for every library that goes through Python's clock -- pandas timestamps, the
futu SDK, most of what we depend on.

WHAT THIS DOES *NOT* FIX, verified 2026-10-02 and worth not re-discovering:

    os.environ['TZ']='UTC'; time.tzset()
    duckdb.connect(':memory:')  ->  current_setting('TimeZone') = 'Asia/Shanghai'

DuckDB resolves its session zone from the SYSTEM zone, not from $TZ, so a bare
`duckdb.connect()` still stamps `now()` eight hours ahead no matter what this file does.
Four such writes shipped before they were caught. The only fix at the connection level is
the explicit `SET TimeZone='UTC'` that db.connect() performs -- so a script that opens its
own connection still has to do that itself, and selfcheck's "no timestamp in the future"
check is the backstop that catches it when it doesn't.

Scripts that need a genuine market-local day keep it EXPLICITLY: an explicit ZoneInfo is
unaffected by the process pin, which is why ibkr_order_collector.py's Asia/Shanghai session
day and the NY->UTC converters in load_ibkr_flex.py still work.

Must run before anything caches a zone, hence at import.
"""
import datetime as dt
import os
import time as _time

if os.environ.get("TZ") != "UTC":
    os.environ["TZ"] = "UTC"
    try:
        _time.tzset()          # re-read TZ for this process; absent on non-POSIX
    except AttributeError:
        pass


def now():
    """Current time as a NAIVE UTC datetime -- the shape every timestamp column stores.

    Naive on purpose: DuckDB TIMESTAMP has no zone, so attaching tzinfo would only be
    stripped later, and an aware/naive comparison raises mid-run.
    """
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def today():
    """Current UTC date. For filenames, report titles, and anything compared to a stamp.

    Distinct from `date.today()` on this box for eight hours of every day.
    """
    return now().date()
