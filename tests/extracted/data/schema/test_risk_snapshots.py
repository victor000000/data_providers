"""Migrate the snapshot key without dropping historical rows."""
import datetime as dt
import unittest

import duckdb
from data_providers.data.schema import risk_snapshots as schema


class TestSnapshotSchema(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        self.addCleanup(self.con.close)

    def legacy(self, expiry="2026-11-20"):
        self.con.execute("""CREATE TABLE risk_snapshots(
            snapshot_timestamp_utc TIMESTAMP, live_timestamp_utc TIMESTAMP,
            account TEXT, underlying TEXT, strike DOUBLE, call_put TEXT, expiry DATE,
            qty INTEGER, PRIMARY KEY(live_timestamp_utc, account, underlying, strike, call_put))""")
        self.con.execute("INSERT INTO risk_snapshots VALUES ('2026-10-15', '2026-10-16', 'a', 'XYZ', 100, 'P', ?, -1)", [expiry])

    def test_migration_preserves_history_and_accepts_a_second_expiry(self):
        self.legacy()
        schema.ensure(self.con)
        self.con.execute("""INSERT INTO risk_snapshots
            (snapshot_timestamp_utc,live_timestamp_utc,account,underlying,strike,call_put,expiry,qty)
            VALUES ('2026-10-15','2026-10-16','a','XYZ',100,'P','2026-12-18',-2)""")
        self.assertEqual(self.con.execute("SELECT expiry,qty FROM risk_snapshots ORDER BY expiry").fetchall(),
                         [(dt.date(2026, 11, 20), -1), (dt.date(2026, 12, 18), -2)])
        schema.ensure(self.con)
        self.assertEqual(self.con.execute("SELECT count(*) FROM risk_snapshots").fetchone()[0], 2)

    def test_an_unknown_legacy_expiry_does_not_destroy_the_original_table(self):
        self.legacy(None)
        with self.assertRaises(duckdb.ConstraintException):
            schema.ensure(self.con)
        self.assertEqual(self.con.execute("SELECT expiry,qty FROM risk_snapshots").fetchall(), [(None, -1)])
        self.assertEqual(self.con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name='risk_snapshots_with_expiry'").fetchone()[0], 0)

    def test_a_fresh_database_has_the_full_key(self):
        schema.ensure(self.con)
        key = self.con.execute("SELECT constraint_column_names FROM duckdb_constraints() WHERE table_name='risk_snapshots' AND constraint_type='PRIMARY KEY'").fetchone()[0]
        self.assertEqual(key, [k.strip() for k in schema.KEY.split(",")])
