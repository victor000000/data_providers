"""Narrow snapshots cannot make stale series appear fresh."""
import datetime as dt
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import duckdb
from data_providers.jobs.history.ibkr_iv_anchor import source_anchor


class SourceAnchorTests(unittest.TestCase):
    def test_global_source_date_wins_over_the_older_shard(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'history.duckdb'
            with duckdb.connect(str(path)) as con:
                con.execute('CREATE TABLE iv_history(source TEXT,underlying TEXT,day DATE)')
                con.execute("INSERT INTO iv_history VALUES ('ibkr','SPY','2026-10-16'),"
                            "('ibkr','OSS','2026-10-13'),('other','X','2026-10-17')")
                scoped = con.execute("SELECT max(day) FROM iv_history WHERE underlying='OSS'").fetchone()[0]
            anchor = source_anchor('ibkr', dt.date(2026,10,17), path)
        self.assertEqual(anchor, dt.date(2026,10,16))
        self.assertGreater((anchor-scoped).days, 1)

    def test_empty_source_uses_conservative_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'history.duckdb'
            with duckdb.connect(str(path)) as con:
                con.execute('CREATE TABLE iv_history(source TEXT,day DATE)')
            self.assertEqual(source_anchor('ibkr', dt.date(2026,10,17), path),
                             dt.date(2026,10,17))

    def test_unavailable_source_does_not_create_a_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'missing.duckdb'
            with patch('builtins.print'):
                self.assertEqual(source_anchor('ibkr', dt.date(2026,10,17), path),
                                 dt.date(2026,10,17))
            self.assertFalse(path.exists())
