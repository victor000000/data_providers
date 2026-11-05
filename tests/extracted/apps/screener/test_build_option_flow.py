"""An incremental flow refresh must preserve history and replace changed days."""
import datetime as dt
import unittest
from unittest.mock import patch

import duckdb
from data_providers.jobs.history import build_option_flow as app


class FlowRefreshTests(unittest.TestCase):
    def test_incremental_refresh_preserves_old_days_and_is_idempotent(self):
        con = duckdb.connect(':memory:')
        con.execute("ATTACH ':memory:' AS mh")
        con.execute("ATTACH ':memory:' AS yh")
        con.execute('CREATE TABLE yh.splits(symbol TEXT,date DATE,ratio DOUBLE)')
        con.execute('CREATE TABLE yh.prices(symbol TEXT,date DATE,close DOUBLE)')
        con.execute('CREATE TABLE mh.underlying_daily(day DATE,symbol TEXT,close DOUBLE)')
        con.execute('CREATE TABLE mh.option_bars(day DATE,expiry DATE,strike DOUBLE,call_put TEXT,close DOUBLE,volume DOUBLE,underlying TEXT,symbol TEXT,source TEXT)')
        con.execute('''CREATE TABLE mh.option_flow_daily(day DATE, underlying TEXT,source TEXT,
            call_oi DOUBLE,put_oi DOUBLE,iv_rank DOUBLE,iv_percentile DOUBLE,hv30 DOUBLE,hv365 DOUBLE,
            call_vol DOUBLE,put_vol DOUBLE,pcr_vol DOUBLE,pcr_oi DOUBLE,gex_proxy DOUBLE,
            method TEXT,note TEXT,PRIMARY KEY(day,underlying,source))''')
        con.execute("INSERT INTO mh.option_flow_daily(day,underlying,source,call_vol) VALUES ('2026-09-01','XYZ','computed_from_bars',123)")
        con.execute("INSERT INTO yh.prices VALUES ('XYZ','2026-10-15',25)")
        con.execute("INSERT INTO mh.option_bars VALUES ('2026-10-15','2026-11-20',100,'C',5,10,'XYZ','XYZ261120C00100000','alpaca'),('2026-10-15','2026-11-20',100,'P',4,20,'XYZ','XYZ261120P00100000','alpaca'),('2026-10-15','2026-11-20',100,'C',6,999,'XYZ','XYZ261120C00100000','thetadata_eod'),('2026-10-15','2026-11-20',105,'C',2,7,'XYZ','XYZ261120C00105000','thetadata_eod')")

        con.execute("INSERT INTO yh.splits VALUES ('XYZ','2026-10-16',4)")

        class KeepOpen:
            execute = con.execute
            executemany = con.executemany
            close = lambda self: None

        try:
            with patch.object(app.bs,'implied_vol',wraps=app.bs.implied_vol) as solver, patch.object(app.db, 'connect', return_value=KeepOpen()), patch('sys.argv', ['flow', '--since', '2026-10-01']):
                app.main()
                con.execute("UPDATE mh.option_bars SET volume=30 WHERE call_put='P'")
                app.main()
            self.assertTrue(solver.call_args_list)
            self.assertTrue(all(call.args[1]==100 for call in solver.call_args_list))
            rows = con.execute('SELECT day,call_vol,put_vol,pcr_vol FROM mh.option_flow_daily ORDER BY day').fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][0:2], (dt.date(2026, 9, 1), 123))
            self.assertEqual(rows[1][1:], (17, 30, 30/17))
        finally:
            con.close()
