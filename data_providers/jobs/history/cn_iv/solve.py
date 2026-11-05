"""solve.py — choose the strikes to fetch, then solve the ATM IV from the quotes we got.

The model differs by venue and mixing them up is a silent level error, so it is explicit:

  DCE     options are on a FUTURE -> Black-76. The future is never fetched: parity gives it
          exactly, F = K + (C - P)*e^(rT), so the forward comes from the same two quotes the
          IV does. Passing S = F*e^(-rT) to Black-Scholes reproduces Black-76 term for term.
  CFFEX   options are on the 中证1000 INDEX -> Black-Scholes with S = the index close, which
          IS fetched, because an index is not a traded contract and cannot be implied from
          parity the way a future can.

Quotes are Sina LAST TRADE prices, not settlements. A strike that did not trade still carries
a stale close that solves to a plausible, wrong IV, so volume > 0 on BOTH legs is required.
"""
from data_providers.compute.bs import implied_vol_batch

# Continuously compounded CNY rate, used only to discount. At these tenors (T < 0.3) a whole
# percentage point of error moves the solved IV by ~0.002 -- a constant, not another feed.
CN_RATE = 0.02
PAD_STRIKES = 2          # ATM is read off the CONTINUOUS main future, not this contract
MAX_STRIKES = 12         # uniform subsample when the window's range needs more than this


def wanted_strikes(grid, path, lo, hi, pad=None, max_strikes=None):
    """The strikes that are ATM on some day in [lo, hi], padded, capped, uniformly thinned.

    Chosen against the CONTINUOUS main future: the month's own forward is not known until its
    quotes are in, and the two differ only by the calendar spread. `pad` absorbs that, and any
    residual error costs one grid step of moneyness.

    `pad` and `max_strikes` are parameters because the callers want different ladders: the IV
    solve needs the money and nothing else, a bars backfill wants the tradable chain. They
    were module constants until 2026-10-07, and the CLI's --pad was accepted and IGNORED --
    a "widen the ladder" run re-fetched the same 12 strikes and stored nothing new.
    """
    pad = PAD_STRIKES if pad is None else pad
    cap = MAX_STRIKES if max_strikes is None else max_strikes
    ks = [k for k, _, _ in grid]
    idx, days = set(), [d for d in path if lo <= d <= hi]
    for d in days:
        j = min(range(len(ks)), key=lambda i: abs(ks[i] - path[d]))
        for p in range(j - pad, j + pad + 1):
            if 0 <= p < len(ks):
                idx.add(p)
    if not idx or not days:
        return []
    pick = sorted(idx)
    if cap and len(pick) > cap:
        step = len(pick) / cap
        pick = [pick[int(i * step)] for i in range(cap)]
    return [grid[i] for i in pick]


def solve_month(venue, table_keep, quotes, expiry, path):
    """{day: (iv, strike, activity)} for one contract month.

    ATM by |C-P| with both legs traded. `activity` is the day's volume summed over every
    strike fetched for this month -- what lets two months be compared on a day they both
    quote, which is the only way to stitch them into one series without a seam.
    """
    rows, days = [], []
    for d in sorted({dd for dd, _c in quotes}):
        if not (d <= expiry):
            continue
        best = None
        for k, c, p in table_keep:
            cq, pq = quotes.get((d, c)), quotes.get((d, p))
            if not cq or not pq:
                continue
            cc, cv = cq
            pc, pv = pq
            if cv <= 0 or pv <= 0:          # a stale close is not a quote -- see docstring
                continue
            if best is None or abs(cc - pc) < best[0]:
                best = (abs(cc - pc), k, cc, pc)
        if best is None:
            continue
        _, k, cc, pc = best
        T = (expiry - d).days / 365.0
        if T <= 0:
            continue
        if venue == "CFFEX":
            S = path.get(d)
            if not S:
                continue
        else:
            F = k + (cc - pc) * pow(2.718281828459045, CN_RATE * T)
            S = F * pow(2.718281828459045, -CN_RATE * T)
        rows += [(cc, S, k, T, CN_RATE, True), (pc, S, k, T, CN_RATE, False)]
        days.append(d)
    if not rows:
        return {}
    ivs = implied_vol_batch(rows)
    out = {}
    for i, d in enumerate(days):
        pair = [v for v in (ivs[2 * i], ivs[2 * i + 1]) if v is not None and 0 < v <= 5]
        if pair:
            out[d] = (sum(pair) / len(pair),
                      sum(v for (dd, _c), (_cl, v) in quotes.items() if dd == d))
    return out
