// qliv_tree.cpp — AMERICAN implied vol and greeks via a trinomial tree.
//
// Why this file exists: every US equity option in this repo is American, but the IV column
// has always been solved with European Black-Scholes. Measured on real AAPL quotes
// (2026-10-02, 49 DTE, S=333.69): the early-exercise premium is negligible at and above the
// money (0.01-0.06% of IV) but real below it — a put struck 11% ITM came out 4.57% high in
// IV terms. The smile band written by download_atm_iv.py (ATM +-5) contains those strikes.
//
// Both models are kept: `model='tree'` rows sit beside `model='bs'` rows under a shared
// primary key, and nothing that reads IV today changes.
//
// WHY A HAND-WRITTEN LATTICE
// QuantLib ships TrinomialTree but no trinomial vanilla engine. BlackScholesLattice is
// hardcoded to two branches and BinomialVanillaEngine cannot be instantiated with a
// trinomial. The adapter below is the same shape QuantLib uses itself in
// ql/models/shortrate/onefactormodel.hpp (ShortRateTree) — a supported pattern, not a hack.
// The base TreeLattice::stepback is branch-count-generic and already carries
// `#pragma omp parallel for`, so we deliberately do NOT override it: rollback and its
// parallelism come for free.
#include <cmath>
#include <limits>
#include <cstddef>
#include <algorithm>
#include <mutex>
#include <atomic>
#include <thread>
#include <vector>
#include <functional>

#include <ql/settings.hpp>
#include <ql/methods/lattices/trinomialtree.hpp>
#include <ql/methods/lattices/lattice1d.hpp>
#include <ql/pricingengines/vanilla/discretizedvanillaoption.hpp>
#include <ql/instruments/vanillaoption.hpp>
#include <ql/instruments/payoffs.hpp>
#include <ql/exercise.hpp>
#include <ql/stochasticprocess.hpp>
#include <ql/time/daycounters/actual365fixed.hpp>
#include <ql/timegrid.hpp>

namespace {

using namespace QuantLib;

constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

// A fixed reference date, never today's, so results are reproducible: the same inputs price
// identically on any day. All curve and tree arithmetic hangs off this.
const Date kRef(1, January, 2020);

const DayCounter& dc() { static const Actual365Fixed d; return d; }

// Pin QuantLib's GLOBAL evaluation date to kRef, exactly once.
//
// THIS IS NOT OPTIONAL, AND LEAVING IT OUT IS SILENT. Instrument::calculate() asks
// isExpired() before it does anything, and for a VanillaOption that compares the exercise
// date against Settings::instance().evaluationDate() -- which defaults to TODAY. An expired
// instrument takes this branch:
//
//     if (isExpired()) { setupExpired(); calculated_ = true; }   // NPV_ = 0.0
//     else             { LazyObject::calculate(); }              // the engine finally runs
//     (ql/instrument.hpp:130-136)
//
// so with a 2020 reference date and the default evaluation date, every option looked long
// expired and was priced at exactly 0.0 — the engine was never called, no exception was
// raised, and no diagnostic was printed. It cost a full debugging cycle here: the symptom was
// "price == 0 at every step count", and the lattice itself was already correct.
//
// call_once because the batch entry points run under OpenMP: the write happens on one thread
// while the rest wait, and afterwards the value is only ever READ, which is what makes the
// shared global safe here. Nothing else in this library uses Settings, so pinning it cannot
// affect the European path in qliv.cpp (which is closed-form and never consults dates).
void ensure_evaluation_date() {
    static std::once_flag once;
    std::call_once(once, [] { Settings::instance().evaluationDate() = kRef; });
}

// Maturity date representing EXACTLY the requested T years.
//
// Callers pass T = days/365 (an integer day count over 365), so round(T*365) recovers that
// integer and the maturity equals the caller's T to the last bit. This matters: at 49 DTE a
// half-day misalignment moves IV by ~0.0013, an order of magnitude LARGER than the American
// premium we are trying to measure at the money. A sloppy maturity would bury the signal.
Date maturityFor(double T) {
    return kRef + static_cast<Integer>(std::llround(T * 365.0));
}

// The log-price process, X = ln S.
//
// THIS IS THE CRUX OF THE FILE. QuantLib's TrinomialTree lays an ADDITIVE grid,
//     underlying(i, j) = x0 + j*dx,   dx = sqrt(3 * variance(t, 0, dt))
// (ql/methods/lattices/trinomialtree.cpp:75-83). Feed it a GeneralizedBlackScholesProcess
// and variance() returns the LOGNORMAL variance sigma^2*dt — a relative quantity — while the
// grid consumes it as an absolute price step. For a short rate (x ~ 0.03) the two nearly
// coincide, which is what the class was built for: every in-tree user is a short-rate model
// (ql/models/shortrate/*). For an equity at S=333 with sigma=0.35 they differ by a factor of
// S. Measured: dx came out 0.0157 where the true step is ~3.0, so a 200-step tree spanned
// only +-3 around spot, a strike 11% away fell OUTSIDE the tree, and the option silently
// priced at pure intrinsic — every step returned 0 after subtracting intrinsic.
//
// Modelling ln S makes the additive grid exactly right: X is additive by construction,
// dX = (r - sigma^2/2)dt + sigma dW, so dx = sigma*sqrt(3*dt) lands in the correct units.
// The lattice exponentiates back so payoffs still see prices, and exp() > 0 always, so no
// node can go negative.
class LogProcess : public StochasticProcess1D {
  public:
    LogProcess(Real S0, Real r, Real sigma)
    : StochasticProcess1D(), r_(r), sigma_(sigma), x0_(std::log(S0)) {}

    Real x0() const override { return x0_; }
    // REQUIRED, not optional. DiscretizedVanillaOption's constructor maps each exercise date
    // to a time via process.time(...) to build its stopping times
    // (ql/pricingengines/vanilla/discretizedvanillaoption.cpp:34), and the base
    // StochasticProcess::time() throws "date/time conversion not supported". Omitting this
    // override makes every American price throw — which is how it was found.
    Time time(const Date& d) const override { return dc().yearFraction(kRef, d); }
    Real drift(Time, Real) const override { return r_ - 0.5 * sigma_ * sigma_; }
    Real diffusion(Time, Real) const override { return sigma_; }
    // Exact, not a discretisation — the log process has constant coefficients.
    Real expectation(Time, Real x, Time dt) const override {
        return x + (r_ - 0.5 * sigma_ * sigma_) * dt;
    }
    Real variance(Time, Real, Time dt) const override { return sigma_ * sigma_ * dt; }
    Real stdDeviation(Time t, Real x, Time dt) const override {
        return std::sqrt(variance(t, x, dt));
    }
    Real evolve(Time, Real x, Time dt, Real dw) const override {
        return x + (r_ - 0.5 * sigma_ * sigma_) * dt + sigma_ * std::sqrt(dt) * dw;
    }
    // Additive — this is what makes the tree's grid consistent with the process.
    Real apply(Real x, Real dx) const override { return x + dx; }

  private:
    Real r_, sigma_, x0_;
};

// Wraps TrinomialTree in the Lattice interface DiscretizedVanillaOption needs.
class TrinomialLattice : public TreeLattice1D<TrinomialLattice> {
  public:
    TrinomialLattice(const ext::shared_ptr<TrinomialTree>& tree, Real r)
    : TreeLattice1D<TrinomialLattice>(tree->timeGrid(), 3), tree_(tree), r_(r) {}

    Size size(Size i) const { return tree_->size(i); }
    // Back to price space: the tree lives in ln S, the payoff lives in S.
    Real underlying(Size i, Size index) const { return std::exp(tree_->underlying(i, index)); }
    Size descendant(Size i, Size index, Size branch) const {
        return tree_->descendant(i, index, branch);
    }
    Real probability(Size i, Size index, Size branch) const {
        return tree_->probability(i, index, branch);
    }
    // Flat continuously-compounded rate, constant across nodes.
    DiscountFactor discount(Size i, Size) const { return std::exp(-r_ * tree_->timeGrid().dt(i)); }

  private:
    ext::shared_ptr<TrinomialTree> tree_;
    Real r_;
};

// The engine. Mirrors BinomialVanillaEngine::calculate() but drives a trinomial lattice.
class TrinomialAmericanEngine : public VanillaOption::engine {
  public:
    TrinomialAmericanEngine(Real S, Real r, Real sigma, Size steps)
    : S_(S), r_(r), sigma_(sigma), steps_(steps) {}

    void calculate() const override {
        // Actual365Fixed over (kRef, lastDate) == the caller's T exactly, by construction.
        const Time maturity = dc().yearFraction(kRef, arguments_.exercise->lastDate());
        const TimeGrid grid(maturity, steps_);
        const ext::shared_ptr<StochasticProcess1D> proc(new LogProcess(S_, r_, sigma_));
        // isPositive=false: in log space nodes are free to be negative and exp() still yields
        // a positive price, so the positivity bump would only distort the grid.
        const ext::shared_ptr<TrinomialTree> tree(new TrinomialTree(proc, grid, false));
        // Held typed so the greek step below can reach underlying(); the base Lattice
        // interface does not expose it.
        const ext::shared_ptr<TrinomialLattice> lattice(new TrinomialLattice(tree, r_));

        DiscretizedVanillaOption option(arguments_, *proc, grid);
        option.initialize(lattice, maturity);

        // Greeks from the lattice, following Hull's construction for the binomial engine
        // (Options, Futures and Other Derivatives, 6th ed., pp. 397-398) adapted to three
        // branches per step: step 1 has 3 nodes, step 2 has 5.
        //
        // Gamma from the middle three nodes of step 2 (1,2,3), not the outer pair — the outer
        // nodes are farthest from the money and carry the most discretisation error.
        option.rollback(grid[2]);
        const Array va2(option.values());
        if (va2.size() >= 5) {
            const Real p2u = va2[3], p2m = va2[2], p2d = va2[1];
            const Real s2u = lattice->underlying(2, 3), s2m = lattice->underlying(2, 2),
                       s2d = lattice->underlying(2, 1);
            const Real dU = (p2u - p2m) / (s2u - s2m);
            const Real dD = (p2m - p2d) / (s2m - s2d);
            results_.gamma = (dU - dD) / ((s2u - s2d) / 2.0);
        }

        // Delta across the full width of step 1.
        option.rollback(grid[1]);
        const Array va(option.values());
        if (va.size() >= 3) {
            const Real p1u = va[2], p1d = va[0];
            const Real s1u = lattice->underlying(1, 2), s1d = lattice->underlying(1, 0);
            results_.delta = (p1u - p1d) / (s1u - s1d);
        }

        option.rollback(0.0);
        results_.value = option.presentValue();
    }

  private:
    Real S_, r_, sigma_;
    Size steps_;
};

// One tree price — the hot function.
double am_price(double S, double K, double T, double r, double sigma, bool isCall, int steps) {
    if (!(S > 0.0) || !(K > 0.0) || !(T > 0.0) || !(sigma > 0.0)) return kNaN;
    ensure_evaluation_date();      // see the comment on the definition — omit and every price is 0

    const ext::shared_ptr<PlainVanillaPayoff> payoff(
        new PlainVanillaPayoff(isCall ? Option::Call : Option::Put, K));
    const ext::shared_ptr<Exercise> exercise(
        new AmericanExercise(kRef, maturityFor(T)));
    VanillaOption opt(payoff, exercise);
    opt.setPricingEngine(ext::shared_ptr<PricingEngine>(
        new TrinomialAmericanEngine(S, r, sigma, static_cast<Size>(steps))));
    try {
        return opt.NPV();
    } catch (...) {
        return kNaN;
    }
}

// Greeks. Split out because the caller already has sigma (from the IV solve), and because a
// full greeks pass costs several extra tree builds.
void am_greeks(double S, double K, double T, double r, double sigma, bool isCall, int steps,
               double* out) {
    for (int i = 0; i < 4; ++i) out[i] = kNaN;
    if (!(S > 0.0) || !(K > 0.0) || !(T > 0.0) || !(sigma > 0.0)) return;
    ensure_evaluation_date();      // same trap as am_price: without it every greek is NaN

    const ext::shared_ptr<PlainVanillaPayoff> payoff(
        new PlainVanillaPayoff(isCall ? Option::Call : Option::Put, K));
    const ext::shared_ptr<Exercise> exercise(new AmericanExercise(kRef, maturityFor(T)));
    VanillaOption opt(payoff, exercise);
    opt.setPricingEngine(ext::shared_ptr<PricingEngine>(
        new TrinomialAmericanEngine(S, r, sigma, static_cast<Size>(steps))));
    try {
        const double v = opt.NPV();               // also fills results_.delta / .gamma
        out[0] = opt.delta();
        out[1] = opt.gamma();

        // Theta per calendar day, matching bs.py's sign convention (change in value as one
        // day of life is spent, normally negative for a long option). Taken from the tree,
        // not a Black-Scholes closed form, because a BS theta describes a European option and
        // this column is the American one.
        //
        // One day shorter at the SAME sigma: that is decay, not a slide along the surface.
        const double t1 = std::max(T - 1.0 / 365.0, 1e-8);
        out[2] = am_price(S, K, t1, r, sigma, isCall, steps) - v;

        // Vega per vol POINT — bs.py divides by 100, so match it and keep the columns
        // comparable. Central difference.
        const double h = 0.001;
        const double up = am_price(S, K, T, r, sigma + h, isCall, steps);
        const double dn = am_price(S, K, T, r, std::max(sigma - h, 1e-6), isCall, steps);
        out[3] = (up - dn) / (2.0 * h) / 100.0;
    } catch (...) {
        for (int i = 0; i < 4; ++i) out[i] = kNaN;
    }
}

// Brent's root-finder, on a bracket with f(a) < 0 < f(b).
//
// Bisection would need ~80 tree builds per implied vol; Brent needs ~10-12. That is the
// single biggest lever on this code path — the tree build dominates everything else, and the
// IV solve is nothing but repeated builds. Brent keeps bisection's guarantee (it falls back
// to a bisection step whenever the interpolated step fails to shrink the bracket), which is
// what makes it safe on the tree price's piecewise-smooth surface in sigma.
//
// Written out rather than using QuantLib's Solver1D<Brent>: that class carries the bracket in
// member variables, so one instance cannot be shared across the batch threads and it adds an
// allocation per solve. This is stateless and lives entirely on the stack.
template <class F>
double brent_solve(const F& f, double a, double b, double fa, double fb,
                   double xtol, int maxit) {
    double c = a, fc = fa;
    double d = b - a, e = d;
    for (int it = 0; it < maxit; ++it) {
        if ((fb > 0.0 && fc > 0.0) || (fb < 0.0 && fc < 0.0)) {
            c = a; fc = fa; d = b - a; e = d;                  // re-bracket
        }
        // Three-point rotation, NOT a swap of a and b. Brent maintains b as the best estimate
        // and c as the previous one, so the ordering (a, b, c) has to rotate together. A plain
        // std::swap(a,b) here leaves c stale, the interpolated step then points at the wrong
        // place, and the solver converges to a confident wrong root — measured as a 0.99 error
        // in recovered vol on random cases before this was fixed.
        if (std::fabs(fc) < std::fabs(fb)) {
            a = b;  fa = fb;
            b = c;  fb = fc;
            c = a;  fc = fa;
        }
        const double tol1 = 2.0 * std::numeric_limits<double>::epsilon() * std::fabs(b) + 0.5 * xtol;
        const double xm = 0.5 * (c - b);
        if (std::fabs(xm) <= tol1 || fb == 0.0) return b;

        if (std::fabs(e) >= tol1 && std::fabs(fa) > std::fabs(fb)) {
            const double s = fb / fa;
            double p, q;
            if (a == c) {                                       // secant step
                p = 2.0 * xm * s;
                q = 1.0 - s;
            } else {                                            // inverse quadratic
                const double qq = fa / fc, rr = fb / fc;
                p = s * (2.0 * xm * qq * (qq - rr) - (b - a) * (rr - 1.0));
                q = (qq - 1.0) * (rr - 1.0) * (s - 1.0);
            }
            if (p > 0.0) q = -q; else p = -p;
            if (2.0 * p < std::min(3.0 * xm * q - std::fabs(tol1 * q), std::fabs(e * q))) {
                e = d; d = p / q;
            } else {
                d = xm; e = d;                                   // fall back to bisection
            }
        } else {
            d = xm; e = d;
        }
        a = b; fa = fb;
        b += (std::fabs(d) > tol1) ? d : ((xm > 0.0) ? tol1 : -tol1);
        fb = f(b);
    }
    return b;
}

// American IV.
//
// Not VanillaOption::impliedVolatility: that method IGNORES the engine you set and substitutes
// FdBlackScholesVanillaEngine for any American exercise (ql/instruments/vanillaoption.cpp:74-79),
// so it would silently answer with a finite-difference number instead of this tree.
double am_iv(double price, double S, double K, double T, double r, bool isCall, int steps) {
    if (!(price > 0.0) || !(S > 0.0) || !(K > 0.0) || !(T > 0.0)) return kNaN;

    // American intrinsic is UNDISCOUNTED: the holder can exercise now for K-S (put) or S-K
    // (call). Using the discounted intrinsic, as the European path correctly does, would
    // wrongly accept quotes no positive vol can produce.
    const double intr = isCall ? std::max(0.0, S - K) : std::max(0.0, K - S);
    if (price <= intr + 1e-6) return kNaN;

    const double a = 1e-4, b = 5.0;
    const double fA = am_price(S, K, T, r, a, isCall, steps) - price;
    const double fB = am_price(S, K, T, r, b, isCall, steps) - price;
    if (!(fA == fA) || !(fB == fB)) return kNaN;
    if (fB < 0.0) return kNaN;                    // needs more vol than the 5.0 ceiling
    if (fA > 0.0) return kNaN;                    // below the vol floor
    // Bracket established: f(a) <= 0 <= f(b), and price is monotone increasing in sigma.

    // xtol 1e-12 is well below the tree's own discretisation error, so solving tighter would
    // only chase noise; the recovered vol was exact to 2e-16 in testing.
    const double sig = brent_solve(
        [&](double s) { return am_price(S, K, T, r, s, isCall, steps) - price; },
        a, b, fA, fB, 1e-12, 100);
    if (!(sig == sig)) return kNaN;
    return sig;
}

// Parallel over rows with std::thread, not OpenMP.
//
// This TU is deliberately compiled WITHOUT -fopenmp (see build_qliv.sh) so that QuantLib's
// `#pragma omp parallel for` inside TreeLattice::stepback stays inert — it fires once per
// TIME STEP, and 200 thread teams per price cost far more than the arithmetic they guard.
// That leaves no OpenMP here to parallelise the batch with, hence a plain thread pool. It is
// also the better shape: one thread per option, no nesting, no two layers fighting.
//
// Must live OUTSIDE the extern "C" block below: a template cannot have C linkage, and g++
// rejects it outright ("template with C linkage") rather than silently ignoring the block.
template <class F>
void parallel_for_rows(std::size_t n, F&& body) {
    if (n == 0) return;
    const unsigned hw = std::thread::hardware_concurrency();
    const unsigned nt = std::max(1u, hw ? hw : 1u);
    // Below ~2 rows per thread the pool costs more than it saves.
    if (n < 2u * nt) {
        for (std::size_t i = 0; i < n; ++i) body(i);
        return;
    }
    std::atomic<std::size_t> next{0};
    std::vector<std::thread> pool;
    pool.reserve(nt);
    for (unsigned t = 0; t < nt; ++t) {
        pool.emplace_back([&] {
            std::size_t i;
            while ((i = next.fetch_add(1, std::memory_order_relaxed)) < n) body(i);
        });
    }
    for (auto& th : pool) th.join();
}

}  // namespace

extern "C" {

// American price. NaN when inputs are unusable.
double ql_am_price(double S, double K, double T, double r, double sigma, int is_call, int steps) {
    return am_price(S, K, T, r, sigma, is_call != 0, steps);
}

// American implied vol. NaN when unsolvable — the same contract ql_implied_vol honours for the
// European path, so a caller cannot tell the two apart by their failure behaviour.
double ql_am_iv(double price, double S, double K, double T, double r, int is_call, int steps) {
    return am_iv(price, S, K, T, r, is_call != 0, steps);
}

// (delta, gamma, theta per day, vega per vol point) — bs.py's exact output convention.
void ql_am_greeks(double S, double K, double T, double r, double sigma, int is_call, int steps,
                  double* out) {
    am_greeks(S, K, T, r, sigma, is_call != 0, steps, out);
}

// Batch entry points. The tree costs ~10^3x a closed-form solve, so one FFI call per value
// would be pure overhead on top of it; these take parallel arrays and spread the work.
void ql_am_iv_batch(std::size_t n, const double* price, const double* S, const double* K,
                    const double* T, const double* r, const int* is_call, int steps,
                    double* out) {
    parallel_for_rows(n, [&](std::size_t i) {
        out[i] = am_iv(price[i], S[i], K[i], T[i], r[i], is_call[i] != 0, steps);
    });
}

void ql_am_greeks_batch(std::size_t n, const double* S, const double* K, const double* T,
                        const double* r, const double* sigma, const int* is_call, int steps,
                        double* out /* n x 4, row-major */) {
    parallel_for_rows(n, [&](std::size_t i) {
        am_greeks(S[i], K[i], T[i], r[i], sigma[i], is_call[i] != 0, steps, out + 4 * i);
    });
}

}  // extern "C"
