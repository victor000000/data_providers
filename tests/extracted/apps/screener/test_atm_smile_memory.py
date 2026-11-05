"""Incremental data must retain the cached central strike and both option sides."""
import unittest
from unittest.mock import Mock
from data_providers.jobs.history.atm_smile_memory import read_smile, listed_strikes, atm_mark


class AtmMemoryTests(unittest.TestCase):
    def test_cached_central_strike_wins_over_a_new_outer_strike(self):
        cached={(60.,'C'):.29468898592769144,(60.,'P'):.31350796815291593}
        fresh={(30.,'C'):1.7702725359851468}
        self.assertEqual(listed_strikes([30.],cached),[30.,60.])
        strike,iv=atm_mark(58.7,fresh,cached)
        self.assertEqual(strike,60.)
        self.assertAlmostEqual(iv,.3040984770403037)

    def test_partial_side_update_keeps_the_stored_other_side(self):
        self.assertEqual(atm_mark(100.,{(100.,'P'):.4},{(100.,'C'):.2}),
                         (100.,.30000000000000004))

    def test_cached_duplicate_matches_the_greek_insert_conflict_policy(self):
        self.assertEqual(atm_mark(100.,{(100.,'C'):.9},{(100.,'C'):.2}),(100.,.2))

    def test_ties_choose_lower_strike_and_invalid_values_are_ignored(self):
        self.assertEqual(atm_mark(100.,{(95.,'C'):.2,(105.,'C'):.3,
                                       (100.,'P'):float('nan')},{}),(95.,.2))
        self.assertIsNone(atm_mark(100.,{},{}))

    def test_cache_reader_keeps_each_day_separate(self):
        con=Mock();con.execute.return_value.fetchall.return_value=[
            ('2026-10-15',100.,'C',.2,'SPY261120C00100000'),('2026-10-16',100.,'P',.3,'SPY261120P00100000'),
            ('2026-10-16',105.,'C',float('nan'),'SPY261120C00105000')]
        self.assertEqual(read_smile(con,'alpaca_atm','SPY','2026-11-20',
                                    '2026-10-15','2026-10-16'),
                         {'2026-10-15':{(100.,'C'):.2},'2026-10-16':{(100.,'P'):.3}})

    def test_reused_current_ticker_is_excluded_before_conversion(self):
        con=Mock();con.execute.return_value.fetchall.return_value=[
            ('2024-10-25',20.,'C',3.,'B241115C00020000'),
            ('2024-10-25',20.,'C',.3,'GOLD241115C00020000')]
        self.assertEqual(read_smile(con,'alpaca_atm','B','2024-11-15',
                                    '2024-10-25','2024-10-25'),
                         {'2024-10-25':{(20.,'C'):.3}})
