"""test_solve.py — the strike ladder, and the ATM IV solved from the quotes we fetched."""
import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

from data_providers.jobs.history.cn_iv import solve  # noqa: E402

DAY = dt.date(2026, 9, 30)


def grid(lo, hi, step):
    return [(float(k), f"c{k:.0f}", f"p{k:.0f}") for k in range(lo, hi + 1, step)]


class TestWantedStrikes(unittest.TestCase):
    def test_covers_every_strike_the_forward_visits(self):
        g = grid(90, 110, 1)
        keep = solve.wanted_strikes(g, {dt.date(2026, 9, 1): 95.0, DAY: 105.0}, DAY - dt.timedelta(days=30), DAY)
        ks = {k for k, _, _ in keep}
        # 95 and 105 are both ATM on some day in the window, so both must be fetched.
        self.assertIn(95.0, ks)
        self.assertIn(105.0, ks)

    def test_padding_absorbs_a_forward_read_off_the_wrong_contract(self):
        # ATM comes from the CONTINUOUS main future, not this month's, so the true ATM can
        # sit a grid step or two away.
        keep = solve.wanted_strikes(grid(90, 110, 1), {DAY: 100.0}, DAY, DAY)
        self.assertLessEqual(min(k for k, _, _ in keep), 98.0)
        self.assertGreaterEqual(max(k for k, _, _ in keep), 102.0)

    def test_the_ladder_is_capped(self):
        keep = solve.wanted_strikes(grid(50, 150, 1), {DAY - dt.timedelta(days=i): 50.0 + i
                                                       for i in range(100)}, DAY - dt.timedelta(days=100), DAY)
        self.assertLessEqual(len(keep), solve.MAX_STRIKES)

    def test_no_forward_in_the_window_means_nothing_to_fetch(self):
        self.assertEqual(solve.wanted_strikes(grid(90, 110, 1), {}, DAY, DAY), [])


class TestSolveMonth(unittest.TestCase):
    def _quotes(self, cv=10.0, pv=10.0, c=5.0, p=4.5):
        return {(DAY, "c100"): (c, cv), (DAY, "p100"): (p, pv)}

    def test_solves_an_atm_iv_and_uses_parity_for_the_forward(self):
        got = solve.solve_month("DCE", [(100.0, "c100", "p100")], self._quotes(),
                                DAY + dt.timedelta(days=30), {DAY: 99.0})
        iv, activity = got[DAY]
        # C=5 P=4.5 on a 100 future 30 days out is roughly a 40-45% vol; the point of the
        # assertion is that it is a sane number, not the constant itself (QuantLib vs the
        # pure-Python fallback differ in the last digits by design).
        self.assertTrue(0.30 < iv < 0.60, iv)
        self.assertEqual(activity, 20.0)

    def test_an_untraded_leg_is_not_a_quote(self):
        # A strike that did not trade still carries a stale close, which solves to a
        # plausible and completely wrong IV. Volume 0 must drop the day.
        self.assertEqual(solve.solve_month("DCE", [(100.0, "c100", "p100")],
                                           self._quotes(pv=0.0),
                                           DAY + dt.timedelta(days=30), {DAY: 99.0}), {})

    def test_expiry_day_itself_has_no_time_value_to_solve(self):
        self.assertEqual(solve.solve_month("DCE", [(100.0, "c100", "p100")], self._quotes(),
                                           DAY, {DAY: 99.0}), {})

    def test_cffex_needs_the_index_close_and_skips_without_it(self):
        args = ("CFFEX", [(100.0, "c100", "p100")], self._quotes(),
                DAY + dt.timedelta(days=30))
        self.assertEqual(solve.solve_month(*args, {}), {})
        self.assertIn(DAY, solve.solve_month(*args, {DAY: 100.0}))

    def test_the_busier_month_wins_the_stitch(self):
        # product.fetch_product compares months by `activity`, so it has to be the volume
        # actually fetched for that month -- if this were the ATM pair's volume alone, a
        # month with two busy strikes would beat the month everyone is trading.
        thin = solve.solve_month("DCE", [(100.0, "c100", "p100")],
                                 self._quotes(cv=1.0, pv=1.0),
                                 DAY + dt.timedelta(days=30), {DAY: 99.0})
        fat = solve.solve_month("DCE", [(100.0, "c100", "p100")],
                                self._quotes(cv=500.0, pv=500.0),
                                DAY + dt.timedelta(days=30), {DAY: 99.0})
        self.assertLess(thin[DAY][1], fat[DAY][1])


if __name__ == "__main__":
    unittest.main()
