"""The public downloader preserves saved bars when its provider returns no window."""
import datetime as dt
from unittest.mock import patch
import duckdb
import pandas as pd
import unittest
from data_providers.jobs.trades import cn_trade_window


class CNWindowProviderTest(unittest.TestCase):
    def test_empty_provider_preserves_data(self):
        with duckdb.connect(':memory:') as con:
            con.execute('CREATE TABLE underlying_minutes (symbol TEXT)')
            con.execute("INSERT INTO underlying_minutes VALUES ('RU2701')")
            with patch.object(cn_trade_window, 'minute_bars', return_value=pd.DataFrame()):
                count = cn_trade_window.store_minutes(con, 'RU2701', dt.date(2026,10,16),
                                                      dt.time(9), dt.time(10))
            self.assertEqual(count, 0)
            self.assertEqual(con.execute('SELECT * FROM underlying_minutes').fetchall(), [('RU2701',)])
