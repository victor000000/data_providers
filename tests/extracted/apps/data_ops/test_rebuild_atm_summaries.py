"""Derived mark repair is archived, scoped by source, and preserves history keys."""
import pathlib
import tempfile
import unittest
import duckdb
from data_providers.jobs.data_ops.rebuild_atm_summaries import build_repairs,apply_repairs


class AtmRebuildTests(unittest.TestCase):
    def setUp(self):
        self.con=duckdb.connect()
        self.con.execute('''CREATE TABLE iv_history(source TEXT,underlying TEXT,
            expiry DATE,day DATE,atm_iv DOUBLE,spot DOUBLE)''')
        self.con.execute('''CREATE TABLE option_greeks_daily(source TEXT,underlying TEXT,
            expiry DATE,day DATE,strike DOUBLE,call_put TEXT,iv DOUBLE)''')
        self.con.execute("INSERT INTO iv_history VALUES ('alpaca_atm','TCOM','2025-07-18','2025-06-26',1.77,58.7),('ibkr','TCOM','2025-07-18','2025-06-26',.8,58.7)")
        self.con.execute("INSERT INTO option_greeks_daily VALUES ('alpaca_atm','TCOM','2025-07-18','2025-06-26',30,'C',1.77),('alpaca_atm','TCOM','2025-07-18','2025-06-26',60,'C',.29),('alpaca_atm','TCOM','2025-07-18','2025-06-26',60,'P',.31)")

    def tearDown(self):
        self.con.close()

    def test_preview_does_not_change_the_mark(self):
        count,_=build_repairs(self.con)
        self.assertEqual(count,1)
        self.assertEqual(self.con.execute("SELECT atm_iv FROM iv_history WHERE source='alpaca_atm'").fetchone(),(1.77,))

    def test_archive_precedes_update_and_other_sources_are_unchanged(self):
        build_repairs(self.con)
        with tempfile.TemporaryDirectory() as folder:
            path=pathlib.Path(folder)/'before.duckdb'
            result=apply_repairs(self.con,path)
            self.assertEqual(result['updated'],1)
            with duckdb.connect(str(path),read_only=True) as saved:
                self.assertEqual(saved.execute('SELECT atm_iv,spot FROM iv_history').fetchone(),(1.77,58.7))
            self.assertAlmostEqual(self.con.execute("SELECT atm_iv FROM iv_history WHERE source='alpaca_atm'").fetchone()[0],.3)
            self.assertEqual(self.con.execute("SELECT atm_iv FROM iv_history WHERE source='ibkr'").fetchone(),(.8,))
            self.assertEqual(self.con.execute('SELECT count(*) FROM iv_history').fetchone(),(2,))

    def test_existing_archive_is_never_overwritten(self):
        build_repairs(self.con)
        with tempfile.TemporaryDirectory() as folder:
            path=pathlib.Path(folder)/'before.duckdb';path.write_text('existing')
            with self.assertRaises(FileExistsError):apply_repairs(self.con,path)
            self.assertEqual(path.read_text(),'existing')
            self.assertEqual(self.con.execute("SELECT atm_iv FROM iv_history WHERE source='alpaca_atm'").fetchone(),(1.77,))
