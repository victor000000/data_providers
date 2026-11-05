import datetime as dt
import unittest
from data_providers.jobs.history.option_root_history import contract, requests, windows

EXP = dt.date(2025, 5, 16)


class HistoricalRootTests(unittest.TestCase):
    def test_conversion_uses_bar_date_including_same_expiry(self):
        assert windows("B", "2025-05-08", "2025-05-09") == [
            ("GOLD", "2025-05-08", "2025-05-08"), (None, "2025-05-09", "2025-05-09")]
        assert windows("B", "2025-05-09", "2025-05-08") == []
    
    
    def test_current_contract_does_not_mask_predecessor(self):
        current = contract("B", None, EXP, "C", 20)
        jobs = requests("B", EXP, [20], "2025-05-08", "2025-05-09", {current:"2025-05-09"})
        assert jobs[0] == ("GOLD", ["GOLD250516C00020000", "GOLD250516P00020000"],
                           "2025-05-08", "2025-05-08")
        assert current not in jobs[1][1]
    
    
    def test_old_root_is_literal_even_if_current_map_aliases_it(self):
        assert contract("B", "GOLD", EXP, "P", 20) == "GOLD250516P00020000"
        assert contract("BRK-B", None, EXP, "C", 500).startswith("BRKB250516C")
    
    
    def test_incremental_segment_begins_after_last_held_day(self):
        previous = contract("B", "GOLD", EXP, "C", 20)
        jobs = requests("B", EXP, [20], "2025-05-06", "2025-05-08", {previous:"2025-05-07"})
        assert jobs[0][2] == "2025-05-06"  # missing put needs whole segment
        both = {previous:"2025-05-07", previous.replace("C000", "P000"):"2025-05-07"}
        assert requests("B", EXP, [20], "2025-05-06", "2025-05-08", both)[0][2] == "2025-05-08"
    
    
    def test_ordinary_ticker_retains_existing_behavior(self):
        assert windows("AAPL", "2024-10-01", "2024-11-01") == [(None,"2024-10-01","2024-11-01")]

    def test_older_unverified_lineage_requires_explicit_extension(self):
        with self.assertRaisesRegex(ValueError,'extend lineage'):
            windows('FISV','2020-01-01','2020-02-01')
