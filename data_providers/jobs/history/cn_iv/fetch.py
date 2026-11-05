"""fetch.py — the two ways this package lost data quietly, both fixed here.

TIMEOUTS. akshare calls `requests.get(url, params=...)` with no timeout, and requests passes
`timeout=None` straight through to urllib3, which leaves the socket BLOCKING. A process-wide
`socket.setdefaulttimeout` does NOT reach it -- measured 2026-10-08, a blackholed host still
hung the full 21s with the default set to 5. So when Sina rate-limits by DROPPING THE SYN
(which is what it does; there is no 429), the worker blocks on the kernel's connect timeout
before `retry()` can even see an exception to catch. One measured `chain_table` call took
37.18s to fail, and 24 workers on one socket produced zero months in 16 minutes.

UNLISTED vs UNANSWERED. akshare signals "this variety is not on that month's page" with a
KeyError, and a dead connection with a `requests` error. `fetch_product` caught both with one
bare `except` and wrote them off as "unlisted", so a dropped connection left a hole in the
series that no log line ever called a failure -- and re-running could not tell it from a month
the product genuinely does not list (DCE lists 8 of 12 for 豆粕).
"""
import time

import requests

DEFAULT_TIMEOUT = 30


class Unlisted(Exception):
    """This product does not list that month. A normal answer, NOT a failure."""


def bounded(seconds=DEFAULT_TIMEOUT):
    """Give every requests call that passes NO timeout one.

    Only a MISSING timeout is filled in, so callers that already name one (massive.py's 60s,
    the TWS paths) keep theirs. Patched on `Session.request`, which is what `requests.get`
    and every akshare call reach, so one patch covers the whole package.
    """
    orig = requests.Session.request

    def request(self, method, url, **kw):
        kw.setdefault("timeout", seconds)
        return orig(self, method, url, **kw)

    requests.Session.request = request


def table(fn, what, tries=4):
    """fn() with retries for everything EXCEPT an unlisted month.

    Retrying an unlisted month would spend three more requests learning what the first one
    already said; NOT retrying a dead connection is how a transient failure became permanent.
    So the two are separated here, at the only place that can tell them apart.
    """
    for i in range(tries):
        try:
            return fn()
        except KeyError as e:
            raise Unlisted(f"{what}: not listed") from e
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 ** i)


def path_or_none(fn, what):
    """fn() for one product's underlying path, or None when it cannot be fetched.

    ONE FLAKY REQUEST MUST NOT KILL THE RUN. On 2026-10-09 `chains.underlying_path` raised
    NameResolutionError for stock2.finance.sina.com.cn -- a DNS blip that was resolving again
    minutes later -- and the exception went straight out of main(), so a four-product leg
    stopped 70 minutes in with nothing left running and nothing said. The month loop already
    degrades one month at a time; this is that rule one level up.
    """
    try:
        return fn()
    except Exception as e:                                      # noqa: BLE001
        print(f"  {what} SKIPPED -- {type(e).__name__} on the underlying path; "
              f"the other products continue", flush=True)
        return None


def _short(items):
    return f"{', '.join(items[:6])}{' …' if len(items) > 6 else ''}"


def report(skipped, failed, months=None):
    """The two fates a month can meet, named separately.

    They look alike in a log and mean opposite things: `skipped` is the PRODUCT not listing
    that month -- DCE lists 8 of 12 -- and `failed` is US not getting an answer. Only one of
    them is a hole in the series, and folding them together is what hid the holes.

    EVERY MONTH UNLISTED IS NOT A PRODUCT FACT. akshare resolves the variety through a SCRAPED
    page, so one Sina stops publishing raises KeyError and lands in `skipped` — measured
    2026-10-11: 聚丙烯期权/PP "unlisted" for all 24 months while we hold 17,135 of its bars.
    """
    if skipped and not failed and months and len(skipped) == len(months):
        failed, skipped = skipped, []          # a product that lists NOTHING is a symbol we
    if skipped:                                # cannot read, and no longer a quiet skip
        print(f"      {len(skipped)} month(s) unlisted: {_short(skipped)}", flush=True)
    if failed:
        print(f"      WARN {len(failed)} month(s) NOT ANSWERED after retries, so they are "
              f"HOLES and not skips -- re-run to fill: {_short(failed)}", flush=True)
