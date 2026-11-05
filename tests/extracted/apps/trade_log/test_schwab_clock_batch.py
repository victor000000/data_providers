"""Resolving the clock for a directory, where a file cannot anchor itself.

The first load of a new trading day has no anchor: the fills it would match against are
exactly the ones not yet written. A statement therefore borrows a measured offset from a
sibling that prints the same zone token, and anything still unresolved stays None so the
caller refuses rather than writes timestamps it cannot vouch for.
"""
import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.trades import schwab_clock_batch as CB


class FakeCon:
    """A log holding ONE fill -- the one at `known_price`.

    Discriminating by price is what makes "today's statement cannot anchor itself"
    testable: a fake that answers every query would let the new day anchor and prove
    nothing.
    """

    def __init__(self, utc, known_price=0.27):
        self.utc, self.known_price = utc, known_price
        self.hit = False

    def execute(self, sql, params):
        self.hit = params[-1] == self.known_price
        return self

    def fetchone(self):
        return (self.utc,) if self.hit else (None,)


NAIVE = dt.datetime(2026, 10, 6, 21, 50, 47)
FILL = {"underlying": "TLT", "strike": 82.0, "call_put": "C", "action": "BUY",
        "qty": 2, "price": 0.27, "ts_utc": NAIVE}


class TestZoneToken(unittest.TestCase):
    def test_it_finds_the_printed_abbreviation(self):
        self.assertEqual(CB.zone_token([["x", "business day 06.10 CST"]]), "CST")

    def test_it_takes_the_sections_dict_the_caller_has(self):
        # `sections()` returns {name: rows}, and iterating that yields the KEYS -- so
        # scanning without flattening finds no zone at all.
        self.assertEqual(CB.zone_token({"Cash Balance": [["a", "06.10 CST"]]}), "CST")

    def test_a_loose_letters_pattern_would_match_the_account_number(self):
        # `[A-Z]{2,4}` also matches "SCHW" in the header, which is not a timezone.
        self.assertIsNone(CB.zone_token([["Account Statement for 40342049SCHW"]]))


class TestResolve(unittest.TestCase):
    def test_a_file_that_can_anchor_is_measured(self):
        out = CB.resolve(FakeCon(NAIVE - dt.timedelta(hours=8)),
                         [("a.csv", [["day 06.10 CST"]])], {"a.csv": [FILL]})
        self.assertEqual(out["a.csv"][0], dt.timedelta(hours=8))

    def test_a_new_days_file_borrows_from_a_sibling(self):
        # THE case: no fill in today's statement is in the log yet, so it cannot anchor.
        con = FakeCon(NAIVE - dt.timedelta(hours=8))
        rows = [["day 06.10 CST"], ["day 06.10 CST"]]
        # The new day's fills were parsed at offset 0, so they carry a naive stamp and
        # simply are not in the log for the anchor query to find.
        out = CB.resolve(con, [("old.csv", rows[0:1]), ("new.csv", rows[1:2])],
                         {"old.csv": [FILL],
                          "new.csv": [dict(FILL, ts_utc=NAIVE, price=9.99)]})
        self.assertIsNotNone(out["old.csv"][0])
        self.assertEqual(out["new.csv"][0], dt.timedelta(hours=8))
        self.assertIn("CST", out["new.csv"][2])

    def test_a_different_zone_token_is_not_borrowed(self):
        # Two files whose zones disagree are not the same clock; borrowing across them
        # would apply the wrong offset silently.
        con = FakeCon(NAIVE - dt.timedelta(hours=8))
        out = CB.resolve(con, [("old.csv", [["day 06.10 CST"]]), ("new.csv", [["day in EST"]])],
                         {"old.csv": [FILL],
                          "new.csv": [dict(FILL, ts_utc=NAIVE, price=9.99)]})
        self.assertIsNone(out["new.csv"][0])

    def test_nothing_anchored_means_nothing_resolved(self):
        out = CB.resolve(FakeCon(None), [("a.csv", [["06.10 CST"]])], {"a.csv": [FILL]})
        self.assertIsNone(out["a.csv"][0])

if __name__ == "__main__":
    unittest.main()
