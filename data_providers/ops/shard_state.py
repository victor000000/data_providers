"""Which shards are already FINISHED — so resuming a sweep does not re-walk them.

Measured 2026-10-12: after the gateway outage, the sweep was restarted to pick up shards 2
and 3. `adaptive_sweep` always starts `shards[:min]`, so it began at shard 0 and re-walked all
four. Shards 0 and 1 had finished hours earlier — the pass fetched ~36,700 rows over ~4 hours
and gained ZERO new ones. Nothing recorded completion, so the supervisor could not know.

The log already says it: the downloader's last line is `done: N fetched, M skipped, ...`.
`Shard.start()` writes a `=== shard N start` header, and `counts()` measures from that same
marker — so the CURRENT run's section is the only one that may be read. A `done:` left over
from a previous run must not count, or a sweep would refuse to restart a shard that crashed
after finishing one pass but before the next.

Only the LAST section counts, and `done:` is matched at the start of a line: a symbol named
`done:` would otherwise be indistinguishable, and there are 6,000 of them.
"""
import pathlib
import re

MARKER = "=== shard "
# The downloader's summary, matched by SHAPE not by prefix. `startswith("done:")` was the
# first version and a test killed it: a symbol line beginning the same way (`done: +12 rows
# (...)`) passed for the summary. Weights and wording are not part of the contract, so the
# three counts are what is matched.
SUMMARY = re.compile(r"^done: \d+ fetched, \d+ skipped, \d+ failed")   # no $: extra detail ok


def run_section(log):
    """This shard's CURRENT run — its text from the last start marker onwards."""
    try:
        txt = pathlib.Path(log).read_text(errors="replace")
    except OSError:
        return ""
    cut = txt.rfind(MARKER)
    return txt[cut:] if cut >= 0 else txt


def finished(log):
    """True when the current run's section ends with the downloader's `done:` summary."""
    lines = [l for l in run_section(log).splitlines() if l.strip()]
    return bool(lines) and bool(SUMMARY.match(lines[-1]))
