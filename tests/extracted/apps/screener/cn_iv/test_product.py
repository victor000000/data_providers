"""test_product.py — one product's months, stitched into a single daily series."""
import datetime as dt
import pathlib
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

from data_providers.jobs.history.cn_iv import chains, contracts, fetch, product  # noqa: E402

E_SEP, E_OCT = dt.date(2026, 9, 16), dt.date(2026, 10, 19)
D1, D2 = dt.date(2026, 9, 20), dt.date(2026, 9, 28)
ARGS = types.SimpleNamespace(window=75, synthetic=None)
GRID = [(3000.0, "c3000", "p3000")]


def hist(pairs):
    return {d: q for d, q in pairs}


class Base(unittest.TestCase):
    """fetch_product over a fake chain: chain_table is the parsed ladder, contract_hist the
    per-contract quotes. Both are the real return shapes, not the raw frames."""

    def setUp(self):
        for name in ("retry", "throttle"):
            p = mock.patch.object(chains, name, lambda *a, **k: (a[0]() if name == "retry"
                                                                 else None))
            p.start()
            self.addCleanup(p.stop)

    def run_product(self, tables, quotes, months, path):
        def table(a, v, n, ymd):
            if ymd not in tables:
                raise fetch.Unlisted("up")         # unlisted month (chain_table's shape)
            return GRID

        def quote(a, v, code):
            return quotes.get(code, {})

        with mock.patch.object(chains, "chain_table", side_effect=table), \
             mock.patch.object(contracts, "contract_hist", side_effect=quote):
            points, _calls, _bars = product.fetch_product(
                ARGS, None, "DCE", "M", "豆粕期权", path, months, dt.date(2026, 9, 30), workers=2)
            return points


class TestFetchProduct(Base):
    def test_unlisted_months_are_skipped_without_fetching_contracts(self):
        # The m2504 lesson: a month this product does not list must not cost 24 requests.
        calls = []

        def table(a, v, n, ymd):
            calls.append(ymd)
            raise fetch.Unlisted("up")

        with mock.patch.object(chains, "chain_table", side_effect=table), \
             mock.patch.object(contracts, "contract_hist") as quote:
            pts, _, _ = product.fetch_product(ARGS, None, "DCE", "M", "豆粕期权", {},
                                           [(2026, 10, E_OCT)], dt.date(2026, 9, 30), workers=2)
        self.assertEqual(pts, {})
        self.assertEqual(calls, ["m2610"])
        quote.assert_not_called()

    def test_two_months_stitch_into_one_series(self):
        quotes = {"c3000": hist([(D1, (40.0, 500)), (D2, (60.0, 50))]),
                  "p3000": hist([(D1, (40.0, 500)), (D2, (60.0, 50))])}
        pts = self.run_product({"m2609": 1, "m2610": 1}, quotes,
                                  [(2026, 9, E_SEP), (2026, 10, E_OCT)],
                                  {D1: 3000.0, D2: 3000.0})
        # Both days come back with a solved ATM IV, from whichever month quoted them.
        self.assertEqual(set(pts), {D1, D2})
        for iv in pts.values():
            self.assertTrue(0.05 < iv < 2.0, iv)

    def test_a_month_with_untraded_legs_contributes_no_days(self):
        quotes = {"c3000": hist([(D1, (40.0, 0))]), "p3000": hist([(D1, (40.0, 0))])}
        pts = self.run_product({"m2609": 1}, quotes, [(2026, 9, E_SEP)], {D1: 3000.0})
        self.assertEqual(pts, {})

    def test_days_after_the_end_date_are_not_returned(self):
        quotes = {"c3000": hist([(dt.date(2026, 10, 5), (40.0, 500))]),
                  "p3000": hist([(dt.date(2026, 10, 5), (40.0, 500))])}
        pts = self.run_product({"m2610": 1}, quotes, [(2026, 10, E_OCT)],
                                  {dt.date(2026, 10, 5): 3000.0})
        self.assertEqual(pts, {}, "2026-10-05 is past --end and must be clipped")


if __name__ == "__main__":
    unittest.main()
