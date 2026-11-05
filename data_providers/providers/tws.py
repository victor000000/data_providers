import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""ibtws.py — one place that knows how to reach the broker's API.

The user runs **IB Gateway** (not TWS desktop) on the Windows host. The two products
listen on different ports and the distinction has bitten us repeatedly:

  * IB Gateway  — live 4001 · paper 4002   <- this is the one that is running
  * TWS desktop — live 7496 · paper 7497

Both are reachable from the container as `host.docker.internal`. Scripts that hardcoded
7496 kept failing with connection refused after the user moved to the Gateway, so the
defaults here are the Gateway's, and both are overridable per shell:

    IB_PORT=7496 uv run ... python apps/data_ops/check_and_save_marks.py   # back to TWS desktop

A connection is read-only unless a script explicitly asks otherwise.
"""
import os

HOST = os.environ.get("IB_HOST", "host.docker.internal" if pathlib.Path("/.dockerenv").exists() else "127.0.0.1")
PORT = int(os.environ.get("IB_PORT", "4001"))


# Every port a TWS might be listening on, and which one it is. The user alternates
# between TWS Gateway and TWS desktop ("sometimes use tws gateway, sometimes use tws
# desktop, you can try each time", 2026-10-05), and which is running is not knowable in
# advance -- a hardcoded port means the run fails on the days they picked the other one,
# with "connection refused", which reads like the gateway is down rather than like we
# knocked on the wrong door. Ordered: the configured port first so an explicit IB_PORT
# still wins, then desktop, then the paper ports.
PORT_NAMES = {4001: "IB Gateway live", 4002: "IB Gateway paper",
              7496: "TWS desktop live", 7497: "TWS desktop paper"}
LAST_PORT = {"port": None}      # the port connect() actually landed on


def candidate_ports():
    seen, out = set(), []
    for p in (PORT, 7496, 7497, 4001, 4002):
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def connect(ib, client_id, timeout=25, readonly=True):
    """Connect `ib` to whichever TWS is running. Returns the IB instance.

    Tries each known port in turn and reports which one answered, so a run against the
    wrong assumption fails with a list of what was tried rather than one connection
    refused. The chosen port is left in LAST_PORT for callers that want to show it.
    """
    tried = []
    for port in candidate_ports():
        try:
            ib.connect(HOST, port, clientId=client_id, timeout=timeout, readonly=readonly)
        except Exception as e:
            tried.append(f"{port} ({PORT_NAMES.get(port, '?')}): {repr(e)[:50]}")
            try:
                ib.disconnect()
            except Exception:
                pass
            continue
        LAST_PORT["port"] = port
        if port != PORT:
            print(f"  TWS: {HOST}:{port} ({PORT_NAMES.get(port, '?')}) — not the "
                  f"configured {PORT}; the user alternates between Gateway and desktop")
        return ib
    raise ConnectionError(
        "no TWS reachable on " + HOST + ". Tried:\n    " + "\n    ".join(tried)
        + "\n  Start IB Gateway or TWS desktop, or set IB_PORT to the one you are running.")


def option_marks(legs, client_id=131, timeout=25):
    """Live option quotes from TWS for [(underlying, expiry, strike, call_put), ...].

    Returns {(underlying, expiry, strike, call_put): Quote} where Quote carries bid, ask, mid,
    last and close, each already passed through ibmarks.clean -- IB leaves NaN or a -1
    sentinel where it has no value, and both read as usable numbers to an unwary caller.

    WHY THIS IS SHARED. complete_risk_analysis had `get_real_option_quotes` as a stub
    returning {} since it was written, so every option price in every risk profile was a
    Black-Scholes estimate while the reports presented it beside real underlying prices
    as though the two were the same kind of number. The fetching pattern already existed
    three times over (check_and_save_marks, capture_risk, hit_probability); this is that
    pattern in one place so the fourth caller does not become a fifth copy.

    Returns whatever TWS gives, including None fields -- the caller decides what to fall
    back to, because only the caller knows whether an estimate is acceptable there.
    """
    from ib_async import IB, Option
    from data_providers.providers import ibmarks

    if not legs:
        return {}
    ib = IB()
    ib.RequestTimeout = 45
    connect(ib, client_id, timeout=timeout)
    try:
        specs = [Option(u, e.strftime("%Y%m%d"), k, cp, "SMART",
                         currency="USD", multiplier="100")
                 for u, e, k, cp in legs]
        contracts = ib.qualifyContracts(*specs)
        out = {}
        for (u, e, k, cp), t in zip(legs, ib.reqTickers(*contracts)):
            bid, ask = ibmarks.clean(t.bid), ibmarks.clean(t.ask)
            out[(u, e, float(k), cp)] = {
                "bid": bid, "ask": ask,
                "mid": (bid + ask) / 2 if (bid and ask) else None,
                "last": ibmarks.clean(t.last),
                "close": ibmarks.clean(t.close),
            }
        return out
    finally:
        ib.disconnect()


def position_margins(legs, client_id=171, timeout=25):
    """Per-position maintenance margin from TWS. legs: [(u, expiry, K, cp, qty), ...].

    IBKR does not expose a per-position margin field -- reqPositions returns no margin,
    and reqAccountSummary is account-level only, which is why the exports the user
    uploaded carry an account total and the Schwab statement carries a per-position
    figure. whatIfOrder is the way through: submit a hypothetical order that CLOSES the
    position and read `maintMarginChange`. Closing a short releases its requirement, so
    the position's own margin is the NEGATIVE of the reported change.

    These orders are never transmitted -- whatIf=True is a margin simulation -- but IB
    still evaluates them against the account, so this must run on a connection for the
    account that holds the positions.

    Returns {(underlying, strike, call_put): {"maint": .., "init": .., "elv": ..,
             "after_maint": .., "after_init": ..}}.

    STATUS 2026-10-05: NOT RELIABLE YET. Every leg returns an empty list. A closing
    order produces no error AND no result, so it is not the margin-deficit rejection
    one would expect -- the simulation simply does not come back. An opening order does
    error (201), so the request itself reaches TWS. Something about the whatIf response
    is not being matched; untested candidates are a TWS-side setting for whatIf on
    simulated orders, or the order-preset/TIF requirement (IB warns 10349 that it
    applied the DAY preset itself).

    Do not use this to size anything yet. The account-level figures from
    reqAccountSummary ARE reliable and are what broker_margin stores; per-position
    margin still comes from the broker's statement where one exists.
    """
    from ib_async import IB, Option, Order

    if not legs:
        return {}
    ib = IB()
    ib.RequestTimeout = 45
    errs = []
    ib.errorEvent += lambda reqId, code, msg, *a: errs.append((code, msg[:90]))
    connect(ib, client_id, timeout=timeout, readonly=False)
    out = {}
    try:
        for u, expiry, K, cp, qty in legs:
            try:
                c = ib.qualifyContracts(Option(u, expiry.strftime("%Y%m%d"), float(K), cp,
                                               "SMART", currency="USD", multiplier="100"))
                if not c:
                    out[(u, float(K), cp)] = {"error": "unqualified"}
                    continue
                o = Order()
                o.action = "BUY" if qty < 0 else "SELL"     # closing action
                o.totalQuantity = abs(qty)
                o.orderType = "MKT"
                o.whatIf = True
                st = ib.whatIfOrder(c[0], o)
                # whatIfOrder returns a LIST of OrderState (one per order leg evaluated),
                # not a single state. Taking it as one raised AttributeError on every leg,
                # which the except below swallowed into a per-leg error dict -- the call
                # looked like it had run and produced nothing.
                if not st:
                    # whatIf returned nothing: IB rejected the simulation before
                    # evaluating it. The reason arrives on the error event, not in the
                    # return value, so report it rather than an empty dict.
                    out[(u, float(K), cp)] = {"error": "rejected: " +
                                              "; ".join(f"{c}:{m}" for c, m in errs[-2:])}
                    errs.clear()
                    continue
                st = st[0] if isinstance(st, list) else st
                out[(u, float(K), cp)] = {
                    "maint": -float(st.maintMarginChange or 0),
                    "init": -float(st.initMarginChange or 0),
                    "elv": -float(st.equityWithLoanChange or 0),
                    "after_maint": float(st.maintMarginAfter or 0),
                    "after_init": float(st.initMarginAfter or 0),
                    "currency": st.maintMarginCurrency,
                }
            except Exception as e:
                out[(u, float(K), cp)] = {"error": repr(e)[:70]}
        return out
    finally:
        ib.disconnect()
