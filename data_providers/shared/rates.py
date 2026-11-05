"""rates.py — the market constants every pricing file was re-declaring.

WHY THIS EXISTS. `R = 0.04` appeared in twelve files, `7.13` in six, and the
seven-point scenario ladder in four — each as a separate literal with its own
comment. They agreed by luck and by habit, not by construction, and the failure
mode is silent: two files that disagree about the risk-free rate produce two
different IVs for the same contract, and nothing says so.

Kept dependency-free on purpose. `compute/bs.py` is the pricing kernel and this
is its input, so importing the kernel from here would be circular; importing
ib_async or duckdb would make a constants module expensive to import from a
test. Plain floats and lists only.

The USD rate is flat because the horizon does not justify a term structure --
see the note at download_atm_iv.py, where that decision was first written down.

NOT the same thing as `CURVE_GRID`: SHOCKS is the seven-point ladder the risk
profile and its checks compare on, while CURVE_GRID is margin_curve's
one-percent sweep used to find a trough. They were both called SHOCKS.
"""

# Risk-free rates. R_CN is the CZCE-side rate used for the China legs; the two
# are not interchangeable and were previously two bare literals in one file.
R_US = 0.04
R_CN = 0.02

# USD/CNY. The China account's delta notional is converted with this; it is a
# single fixed rate rather than a live FX lookup, which is a known approximation.
FX_CNY = 7.13

# The scenario ladder: uniform shocks applied to every underlying, 0-day
# horizon, IV unchanged. Four files defined this list independently.
SHOCKS = [-0.15, -0.10, -0.05, 0.0, 0.05, 0.10, 0.15]

# margin_curve's fine grid, -20%..+20% in 1% steps, for finding a curve trough.
CURVE_GRID = [i / 100 for i in range(-20, 21)]


def to_usd(amount, broker, cn_broker="浙商期货"):
    """Convert `amount` to USD for a leg held at `broker`.

    The China account is the only one whose figures are stored in local currency,
    and every aggregation in the repo needs the same answer to "is this one CNY".
    The test was spelled `broker == "浙商期货"` in five files, one of which
    compared against a different spelling of the same broker.
    """
    return amount / FX_CNY if broker == cn_broker else amount


def fx_of(broker, cn_broker="浙商期货"):
    """The divisor to apply to `broker`'s figures to express them in USD."""
    return FX_CNY if broker == cn_broker else 1.0
