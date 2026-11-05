// qliv.cpp — QuantLib behind a C ABI, for ctypes.
//
// WHY A C ABI AND NOT THE PYTHON BINDINGS
// ---------------------------------------
// The rest of this repo runs as `uv run --no-project --with duckdb python scripts/x.py`:
// ephemeral environments with the dependency list spelled out at each call site. QuantLib's
// SWIG bindings would have to be added to every one of those invocations, and they are a
// 19 MB package that must be rebuilt whenever the vendored C++ changes. A shared object
// loaded through ctypes needs NOTHING from the Python environment -- `import bs` keeps
// working in every existing invocation, with the pure-Python solver as the fallback when
// the .so is missing.
//
// CONVENTIONS — these must match scripts/bs.py exactly, or the two solvers disagree:
//   * T is in YEARS (the caller does (expiry - today).days / 365), r is continuously
//     compounded, S and K are in currency units.
//   * No dividend yield: the forward is F = S*exp(r*T) and the discount exp(-r*T), which
//     reproduces Black-Scholes from Black-76 exactly.
//   * `is_call` is 1 for a call, 0 for a put.
//   * An unsolvable input returns NaN, which bs.py maps to None. "Unsolvable" means what
//     bs.py means by it: T/S/K non-positive, a price at or below intrinsic (a stale or
//     crossed quote, which is common in real chains), or a price above the sigma=5.0
//     bound. Returning NaN rather than a clamped number matters -- a caller that cannot
//     tell "no answer" from "an answer" will file 500% vol as data.
#include <cmath>
#include <limits>
#include <cstddef>

#include <ql/option.hpp>
#include <ql/types.hpp>
#include <ql/version.hpp>
#include <ql/pricingengines/blackformula.hpp>
#include <ql/pricingengines/blackcalculator.hpp>

#ifdef _OPENMP
#include <omp.h>
#endif

namespace {

constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

// The same upper bound bs.py bisects to. A quote that needs sigma > 5.0 is not a volatility
// we want to record, and the root-finder should say so rather than return the bound.
constexpr double kMaxVol = 5.0;

// Solver accuracy. bs.py's 100-step bisection on [1e-4, 5.0] resolves to ~5/2^100, i.e.
// far below double precision, so the Python solver is effectively exact and anything
// coarser here would show up as a disagreement in the cross-check. 1e-14 is the tightest
// QuantLib accepts without burning iterations on a flat objective.
constexpr double kAccuracy = 1e-14;
constexpr int    kMaxIter  = 200;

inline QuantLib::Option::Type as_type(int is_call) {
    return is_call ? QuantLib::Option::Type::Call : QuantLib::Option::Type::Put;
}

// Forward and discount for a non-dividend-paying underlying.
inline void fwd_disc(double S, double T, double r, double& F, double& disc) {
    F = S * std::exp(r * T);
    disc = std::exp(-r * T);
}

// The guard shared by every entry point. Reproduces bs.py's conditions so that the two
// implementations agree not just on the answer but on WHEN there is no answer.
inline bool solvable(double price, double S, double K, double T, double r, int is_call) {
    if (!(price > 0.0) || !(S > 0.0) || !(K > 0.0) || !(T > 0.0)) return false;
    const double disc = std::exp(-r * T);
    const double intr = is_call ? std::max(0.0, S - K * disc)
                                : std::max(0.0, K * disc - S);
    if (price <= intr + 1e-6) return false;      // at/below intrinsic: stale or crossed
    return true;
}

}  // namespace

extern "C" {

// Black-Scholes price. Same formula as bs.bs_price, via Black-76.
double ql_price(double S, double K, double T, double r, double sigma, int is_call) {
    if (!(S > 0.0) || !(K > 0.0)) return kNaN;
    if (!(T > 0.0) || !(sigma > 0.0)) {                  // bs.py returns intrinsic here
        return std::max(0.0, is_call ? (S - K) : (K - S));
    }
    double F, disc;
    fwd_disc(S, T, r, F, disc);
    try {
        return QuantLib::blackFormula(as_type(is_call), K, F, sigma * std::sqrt(T), disc);
    } catch (...) {
        return kNaN;
    }
}

// Implied volatility -> sigma (annualised). NaN when unsolvable.
double ql_implied_vol(double price, double S, double K, double T, double r, int is_call) {
    if (!solvable(price, S, K, T, r, is_call)) return kNaN;
    double F, disc;
    fwd_disc(S, T, r, F, disc);
    try {
        // QuantLib solves for TOTAL standard deviation (sigma*sqrt(T)); the bound bs.py
        // applies on sigma becomes the same bound on stdDev.
        const double stdDev = QuantLib::blackFormulaImpliedStdDev(
            as_type(is_call), K, F, price, disc, 0.0,
            QuantLib::Null<QuantLib::Real>(), kAccuracy, kMaxIter);
        if (!(stdDev > 0.0)) return kNaN;
        const double sigma = stdDev / std::sqrt(T);
        return (sigma > kMaxVol) ? kNaN : sigma;         // match bs.py's ceiling
    } catch (...) {
        // QuantLib throws when the price is outside the no-arbitrage band. That is the
        // same condition bs.py reports as None, so translate rather than propagate.
        return kNaN;
    }
}

// (delta, gamma, theta per day, vega per vol POINT) — bs.py's exact output convention,
// including the /100 on vega. Writes 4 doubles; NaN-fills when inputs are unusable.
void ql_greeks(double S, double K, double T, double r, double sigma, int is_call, double* out) {
    for (int i = 0; i < 4; ++i) out[i] = kNaN;
    if (!(S > 0.0) || !(K > 0.0) || !(T > 0.0) || !(sigma > 0.0)) return;
    double F, disc;
    fwd_disc(S, T, r, F, disc);
    try {
        const QuantLib::BlackCalculator c(as_type(is_call), K, F, sigma * std::sqrt(T), disc);
        out[0] = c.delta(S);
        out[1] = c.gamma(S);
        out[2] = c.thetaPerDay(S, T);
        out[3] = c.vega(T) / 100.0;
    } catch (...) {
        // leave NaNs
    }
}

// Batch IV, parallelised across the box's cores.
//
// The scalar entry point is for the drop-in call sites; THIS is the one that matters for
// throughput, because a ctypes call costs ~1 us and would otherwise dominate a ~1 us
// solve. Callers pass parallel arrays and get one answer per element.
void ql_implied_vol_batch(std::size_t n,
                          const double* price, const double* S, const double* K,
                          const double* T, const double* r, const int* is_call,
                          double* out) {
#ifdef _OPENMP
    #pragma omp parallel for schedule(static)
#endif
    for (std::ptrdiff_t i = 0; i < static_cast<std::ptrdiff_t>(n); ++i) {
        out[i] = ql_implied_vol(price[i], S[i], K[i], T[i], r[i], is_call[i]);
    }
}

// Version probe, so a caller can tell which library it actually loaded.
const char* ql_version() { return QL_VERSION; }

}  // extern "C"
