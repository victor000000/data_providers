"""The fill list a load prints.

Not decoration: this listing is what caught both of the loader's bugs. Reading it is
how "every price is 100x too high" and "every spread is missing its second leg" became
visible -- the numbers looked wrong on the page before any check existed for them.
"""
import contextlib
import io
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.trades import schwab_report as R


def fill(underlying="TLT", strike=82.0, cp="C", action="BUY", qty=2, price=0.27,
         oc="CLOSE", when="2026-10-06 13:50"):
    import datetime as dt
    return {"ts_utc": dt.datetime.fromisoformat(when), "underlying": underlying,
            "strike": strike, "call_put": cp, "action": action, "qty": qty,
            "price": price, "open_close": oc}


def capture(fills, orders=(), pos=(), summ=None):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        R.sections(fills, orders, pos, summ if summ is not None else {"Net Liquidating Value": 1})
    return buf.getvalue()


class TestEveryLegIsListed(unittest.TestCase):
    def test_a_two_leg_spread_prints_two_lines(self):
        out = capture([fill(cp="C", strike=82.0), fill(cp="P", strike=74.0)])
        self.assertIn("82C", out)
        self.assertIn("74P", out)

    def test_a_sub_dollar_price_prints_as_a_fraction(self):
        # `27.00` on the page was the visible symptom of the leading-dot bug. This
        # asserts the rendered form because that is what a reader actually catches.
        self.assertIn("0.27", capture([fill(price=0.27)]))
        self.assertNotIn("27.0 ", capture([fill(price=0.27)]))


class TestTheSummaryLine(unittest.TestCase):
    def test_it_counts_every_section(self):
        out = capture([fill(), fill()], orders=[{}], pos=[{}, {}, {}])
        self.assertIn("fills 2", out)
        self.assertIn("orders 1", out)
        self.assertIn("positions 3", out)

    def test_a_missing_net_liq_does_not_raise(self):
        self.assertIn("None", capture([], summ={}))


if __name__ == "__main__":
    unittest.main()
