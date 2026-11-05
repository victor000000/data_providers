"""The execution and order parsers -- both of which shipped recording half the truth.

TWO BUGS, both silent, both found by printing what was parsed.
 1. A spread is TWO ROWS and only the first carries the exec time; Schwab leaves
    Time/Spread/Net blank on the second leg. Skipping rows without a time recorded one
    leg of every spread -- and the missing leg is the one that makes the position whole.
    A day of strangles came through as a set of naked short calls.
 2. The Order History's own column layout has a leading blank field, so reading the
    time at index 1 (right for Trade History) matched nothing: a day with 30 orders
    parsed as zero, and the eleven REJECTED-for-buying-power rows went unrecorded.
"""
import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.trades import schwab_account_parse as P
from tests.extracted.apps.trade_log._statement import sections

UTC8 = dt.timedelta(hours=8)


class TestEverySpreadLegIsRecorded(unittest.TestCase):
    def setUp(self):
        self.fills = P.parse_fills(sections()["Account Trade History"], UTC8)

    def test_a_strangle_yields_both_legs(self):
        tlt = [f for f in self.fills if f["underlying"] == "TLT"]
        self.assertEqual(len(tlt), 2, "a strangle is a call AND a put")
        self.assertEqual({f["call_put"] for f in tlt}, {"C", "P"})

    def test_the_continuation_row_inherits_the_exec_time(self):
        # THE regression: the second leg has a blank time cell. Without inheriting it
        # the row is dropped, because a fill with no time is not a fill.
        legs = {f["call_put"]: f for f in self.fills if f["underlying"] == "TLT"}
        self.assertEqual(legs["C"]["ts_utc"], legs["P"]["ts_utc"])
        self.assertEqual(legs["P"]["ts_utc"],
                         dt.datetime(2026, 10, 6, 21, 50, 47) - UTC8)

    def test_the_two_legs_are_not_confused(self):
        legs = {f["call_put"]: f for f in self.fills if f["underlying"] == "TLT"}
        self.assertEqual(legs["C"]["strike"], 82.0)
        self.assertEqual(legs["P"]["strike"], 74.0)

    def test_a_bare_dot_price_is_not_multiplied_by_100(self):
        # `.27`, not `0.27` -- the format Schwab actually writes for a cheap option.
        legs = {f["call_put"]: f for f in self.fills if f["underlying"] == "TLT"}
        self.assertEqual(legs["C"]["price"], 0.27)

    def test_every_leg_gets_its_own_notes_key(self):
        # Deduplication is on notes. A continuation row has blank spread AND blank time,
        # so without carrying them down every second leg of every spread collides on
        # "; net DEBIT" and a re-run would silently skip it.
        keys = [f["notes"] for f in self.fills]
        self.assertEqual(len(keys), len(set(keys)), f"colliding notes: {keys}")

    def test_both_spreads_are_two_legs_each(self):
        self.assertEqual(len(self.fills), 4)


class TestOrders(unittest.TestCase):
    def setUp(self):
        self.orders = P.parse_orders(sections()["Account Order History"], UTC8)

    def test_orders_are_found_at_all(self):
        # THE regression. Reading the time at index 1 matched the blank leading column
        # and returned nothing, so a session's whole order lifecycle was dropped.
        self.assertTrue(self.orders, "no orders parsed from a section that has two")

    def test_the_time_is_read_from_the_right_column(self):
        self.assertEqual(self.orders[0]["ts_submit_utc"],
                         dt.datetime(2026, 10, 6, 21, 51, 53) - UTC8)

    def test_a_rejection_is_kept_with_its_reason_stripped_to_a_status(self):
        # Eleven rejections for buying power in one session is a fact about the account,
        # not noise. The long broker sentence is not a status.
        rej = [o for o in self.orders if o["status"] == "REJECTED"]
        self.assertEqual(len(rej), 1)
        self.assertIn("buying power", rej[0]["notes"])

    def test_the_limit_price_comes_from_the_price_column(self):
        self.assertEqual(self.orders[0]["limit_price"], 10.63)

    def test_the_spread_name_is_the_order_type(self):
        self.assertEqual(self.orders[0]["order_type"], "DIAGONAL")


if __name__ == "__main__":
    unittest.main()
