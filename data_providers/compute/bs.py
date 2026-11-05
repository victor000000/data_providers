import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""bs.py — Black-Scholes pricing, greeks, and implied-vol solver.

Two implementations, one interface:
  * QuantLib 1.43, built from the vendored source in third_party/QuantLib and reached
    through the qliv C ABI (third_party/qliv/qliv.so) — the default when that library is
    present. See scripts/build_qliv.sh.
  * a stdlib-only bisection solver, used when the shared object is missing or when
    BS_FORCE_PYTHON=1 is set in the environment.

The fallback is not decoration. bs.py is imported by ~15 scripts, several of which run in
ephemeral `uv run --no-project` environments where nothing is installed; a version that
hard-requires a compiled .so would turn "QuantLib is not built yet" into a crashed
downloader. Both paths are also cross-checked against each other (scripts/check_iv_solvers.py)
— the pure-Python one is the reference, because its answer does not depend on any library's
solver conventions.

Conventions, and they are load-bearing:
  T is in YEARS, r is continuously compounded, no dividend yield, `call` is a bool.
"""
import ctypes
import math
import os
import pathlib
from data_providers.data import clock

ROOT = bootstrap.ROOT
_SO = pathlib.Path(os.environ.get("DATA_PROVIDERS_NATIVE_LIBRARY",
    str(pathlib.Path(__file__).resolve().parents[2] / "third_party" / "qliv" / "qliv.so")))

# ── QuantLib via ctypes, when it is there ────────────────────────────────────────────────
_ql = None
if _SO.exists() and not os.environ.get("BS_FORCE_PYTHON"):
    try:
        _ql = ctypes.CDLL(str(_SO))
        _ql.ql_implied_vol.restype = ctypes.c_double
        _ql.ql_implied_vol.argtypes = [ctypes.c_double] * 5 + [ctypes.c_int]
        _ql.ql_price.restype = ctypes.c_double
        _ql.ql_price.argtypes = [ctypes.c_double] * 5 + [ctypes.c_int]
        _ql.ql_greeks.restype = None
        _ql.ql_greeks.argtypes = [ctypes.c_double] * 5 + [ctypes.c_int,
                                                          ctypes.POINTER(ctypes.c_double)]
        _ql.ql_implied_vol_batch.restype = None
        _ql.ql_implied_vol_batch.argtypes = [ctypes.c_size_t] + \
            [ctypes.POINTER(ctypes.c_double)] * 5 + [ctypes.POINTER(ctypes.c_int),
                                                     ctypes.POINTER(ctypes.c_double)]
    except Exception as e:                                   # noqa: BLE001
        # Loud, once: a silently-missing QuantLib would leave every timing measurement in
        # this repo describing the fallback while the report claims the library.
        print(f"WARNING: qliv.so present but failed to load ({e!r}); using the Python solver")
        _ql = None

USING_QUANTLIB = _ql is not None


def _N(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def T_years(expiry, today=None):
    today = today or clock.today()
    return max((expiry - today).days, 0) / 365.0


def bs_price(S, K, T, r, sigma, call):
    # Both backends must answer identically for identical input, and they did not: S=0
    # returned NaN through QuantLib and raised ValueError out of math.log on the
    # pure-Python path, so the same call either produced a value or killed the caller
    # depending on which backend was compiled in. Callers do arithmetic on the result
    # (`- target`, `* fa`, `float(...)`), so this cannot return None the way the
    # Optional-returning functions here do; NaN keeps the stated contract and stays a
    # float.
    #
    # T and sigma are deliberately NOT rejected here. `T <= 0 or sigma <= 0` below is a
    # legitimate input meaning "expired" or "no vol", which prices at intrinsic, and
    # callers pass `iv or 0.0` to get exactly that -- guarding them would turn a correct
    # answer into NaN across several apps. Only values that make the formula meaningless
    # are refused.
    if _nonpositive(S, K) or _not_finite(T, r, sigma):
        return float("nan")
    if _ql is not None:
        return _ql.ql_price(S, K, T, r, sigma, 1 if call else 0)
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if call else (K - S))
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / sq
    d2 = d1 - sq
    if call:
        return S * _N(d1) - K * math.exp(-r * T) * _N(d2)
    return K * math.exp(-r * T) * _N(-d2) - S * _N(-d1)


def _nonpositive(*vals):
    """True if any value is None, NaN, or <= 0 -- i.e. no usable input.

    The `v != v` self-comparison is the NaN test. It cannot be written as `v <= 0`:
    every comparison against NaN is False, and `bool(nan)` is True, so a NaN slips
    through `if not v or v <= 0` entirely. It then propagates: the pure-Python path
    returned (nan, nan, nan, nan) where QuantLib returned (None, None, None, None),
    and implied_vol returned a CONFIDENT 0.0001 -- the bisection's lower bound --
    where QuantLib returned None. NaN arrives easily (a CSV "nan", a JSON literal
    NaN, an unguarded mean of empty input), and this module's whole contract is
    that the two backends are indistinguishable and that "no answer" is None.
    """
    for v in vals:
        if v is None or v != v or v <= 0:
            return True
    return False


def _not_finite(*vals):
    """True if any value is None or not finite (NaN / +/-inf) -- the test for a RATE.

    Separate from _nonpositive because a rate of zero is legitimate: zero-rate regimes
    exist and the formula handles r=0 correctly, so the `<= 0` test that is right for S, K,
    T and sigma is wrong here and would refuse valid inputs. What r must not be is absent
    or NaN. `max(0.0, nan)` collapses to 0.0 -- Python keeps the first argument when the
    comparison is False -- every bisection probe then returns NaN, `nan < price` is False,
    hi walks to the lower bound and implied_vol returns a confident 0.0001 where QuantLib
    returns None. That is verbatim the failure _nonpositive's docstring says was
    eliminated; it survived on the one argument that guard does not cover.
    """
    for v in vals:
        if v is None or not math.isfinite(v):
            return True
    return False


def bs_greeks(S, K, T, r, sigma, call):
    """Returns (delta, gamma, theta_per_day, vega_per_vol_point)."""
    # `not X` first: X can be None (no live spot / no solvable IV), and `None <= 0` is a
    # TypeError that kills the caller instead of returning its documented no-data value.
    if _nonpositive(S, K, T, sigma) or _not_finite(r):
        return (None, None, None, None)
    if _ql is not None:
        out = (ctypes.c_double * 4)()
        _ql.ql_greeks(S, K, T, r, sigma, 1 if call else 0, out)
        # QuantLib signals "no value" with NaN; this module's contract is None. The four
        # come back together, so one NaN means all four are absent.
        if math.isnan(out[0]):
            return (None, None, None, None)
        return (out[0], out[1], out[2], out[3])
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / sq
    d2 = d1 - sq
    pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    gamma = pdf / (S * sq)
    vega = S * pdf * math.sqrt(T) / 100.0
    if call:
        delta = _N(d1)
        theta = (-S * pdf * sigma / (2.0 * math.sqrt(T)) - r * K * math.exp(-r * T) * _N(d2)) / 365.0
    else:
        delta = _N(d1) - 1.0
        theta = (-S * pdf * sigma / (2.0 * math.sqrt(T)) + r * K * math.exp(-r * T) * _N(-d2)) / 365.0
    return (delta, gamma, theta, vega)


def bs_delta(S, K, T, r, sigma, call):
    return bs_greeks(S, K, T, r, sigma, call)[0]


def implied_vol(price, S, K, T, r, call):
    """Implied vol. Returns None if price is below intrinsic / non-solvable.

    QuantLib path when available (see module docstring); otherwise a bisection whose
    conditions for "no answer" the QuantLib shim reproduces exactly, so callers cannot tell
    which one ran from the return value alone.
    """
    if _nonpositive(S, K, T) or _not_finite(r, price):
        return None
    if _ql is not None:
        v = _ql.ql_implied_vol(price, S, K, T, r, 1 if call else 0)
        return None if math.isnan(v) else v
    intr = max(0.0, (S - K * math.exp(-r * T)) if call else (K * math.exp(-r * T) - S))
    if price <= intr + 1e-6:
        return None
    lo, hi = 1e-4, 5.0
    if bs_price(S, K, T, r, hi, call) < price:
        return None
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, r, mid, call) < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def implied_vol_batch(rows):
    """Implied vol for many quotes at once: [(price, S, K, T, r, call), ...] -> [iv|None].

    Use this instead of a loop over implied_vol() wherever the row count is large. The
    saving is not the solver — it is the FFI: a ctypes call costs about as much as a
    QuantLib solve, so a per-row call pays the boundary cost once per IV and a batch pays
    it once per batch, with the solves spread over every core.

    Falls back to the scalar Python path elementwise when QuantLib is absent.
    """
    n = len(rows)
    if _ql is None or n == 0:
        return [implied_vol(*r) for r in rows]
    # Guard ELEMENTWISE before the FFI. The batch writes row values straight into a
    # c_double array, so a None price raised "TypeError: must be real number, not
    # NoneType" where the scalar returns None, and a NaN rate went into the solver
    # unflagged -- one function's two paths disagreeing about what "no answer" is.
    # Unusable rows get neutral placeholders and their results are overwritten with None.
    ok = [not (_nonpositive(p, s, k, t) or _not_finite(rate))
          for (p, s, k, t, rate, c) in rows]
    d = (ctypes.c_double * n)
    price, S, K, T, r_, call = d(), d(), d(), d(), d(), (ctypes.c_int * n)()
    for i, (p, s, k, t, rate, c) in enumerate(rows):
        price[i], S[i], K[i], T[i], r_[i] = ((p, s, k, t, rate) if ok[i]
                                             else (1.0, 1.0, 1.0, 1.0, 0.0))
        call[i] = 1 if c else 0
    out = d()
    _ql.ql_implied_vol_batch(n, price, S, K, T, r_, call, out)
    return [None if (not ok[i] or math.isnan(v)) else v for i, v in enumerate(out)]
