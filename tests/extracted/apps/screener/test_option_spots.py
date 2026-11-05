"""Only matching dated broker RTH closes may fill missing option-solver spots."""
import datetime as dt
import unittest
import duckdb
from data_providers.jobs.history.option_spots import add_broker_spots, historical_close


class OptionSpotTests(unittest.TestCase):
    def test_preserves_yahoo_and_identifies_broker_as_traded_days(self):
        with duckdb.connect(':memory:') as con:
            con.execute('CREATE SCHEMA mhro')
            con.execute('CREATE TABLE mhro.underlying_daily(day DATE,symbol TEXT,close DOUBLE,source TEXT)')
            con.execute("""INSERT INTO mhro.underlying_daily VALUES
                ('2026-10-15','MU',100,'ibkr_rth_daily'),
                ('2026-10-16','MU',110,'ibkr_rth_daily'),
                ('2026-10-17','MU',120,'ibkr_rth_daily'),
                ('2026-10-14','MU',130,'other'),
                ('2026-10-14','SPY',140,'ibkr_rth_daily'),
                ('2026-10-13','MU','NaN','ibkr_rth_daily'),
                ('2026-10-12','MU',0,'ibkr_rth_daily')""")
            prices={'2026-10-15':90.}
            out, broker_days=add_broker_spots(con,'MU',dt.date(2026,10,12),dt.date(2026,10,16),prices)
            self.assertEqual(out,{'2026-10-15':90.,'2026-10-16':110.})
            self.assertEqual(broker_days,{'2026-10-16'})
            self.assertEqual(prices,{'2026-10-15':90.})

    def test_split_conversion_excludes_the_effective_session(self):
        splits=[(dt.date(2026,7,2),4.),(dt.date(2026,9,1),2.)]
        self.assertEqual(historical_close(dt.date(2026,7,1),25.,splits),200.)
        self.assertEqual(historical_close(dt.date(2026,7,2),25.,splits),50.)
        self.assertEqual(historical_close(dt.date(2026,9,1),25.,splits),25.)
