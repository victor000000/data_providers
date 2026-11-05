"""test_dump.py — fetch-to-JSON and import-to-database, which exist to be run separately."""
import datetime as dt
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

import duckdb  # noqa: E402

from data_providers.jobs.history.cn_iv import dump  # noqa: E402

D1, D2 = dt.date(2026, 9, 29), dt.date(2026, 9, 30)
SCHEMA = "CREATE TABLE iv_history (day DATE, underlying VARCHAR, expiry DATE, atm_iv DOUBLE, spot DOUBLE, source VARCHAR)"
FUTURES = "CREATE TABLE futures_daily (product VARCHAR, day DATE, close DOUBLE, source VARCHAR)"


def fresh_con():
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    con.execute(FUTURES)
    return con


class TestWritePoints(unittest.TestCase):
    def test_round_trip(self):
        con = fresh_con()
        dump.write_points(con, "M", "akshare_m_solver", {D1: 0.15, D2: 0.16})
        self.assertEqual(con.execute("SELECT day, atm_iv FROM iv_history ORDER BY day").fetchall(), [(D1, 0.15), (D2, 0.16)])

    def test_spot_is_taken_from_the_continuous_series_not_hardcoded_null(self):
        """The writers hardcoded spot NULL: MA carried NULL on all 1,638 rows until 2026-10-12."""
        con = fresh_con()
        con.executemany("INSERT INTO futures_daily VALUES ('M', ?, ?, ?)", [(D1, 3200.0, "sina_main"), (D1, 999.0, "sina_contract")])
        dump.write_points(con, "M", "src", {D1: 0.15, D2: 0.16})
        self.assertEqual(con.execute("SELECT day, spot FROM iv_history ORDER BY day").fetchall(), [(D1, 3200.0), (D2, None)])

    def test_re_running_replaces_those_days_instead_of_doubling_them(self):
        # Every builder is documented as idempotent; this is the assertion behind that word.
        con = fresh_con()
        dump.write_points(con, "M", "src", {D1: 0.15})
        dump.write_points(con, "M", "src", {D1: 0.99})
        self.assertEqual(con.execute("SELECT count(*), max(atm_iv) FROM iv_history").fetchone(),
                         (1, 0.99))


class TestImportRun(unittest.TestCase):
    def test_imports_every_product_and_leaves_others_alone(self):
        con = fresh_con()
        dump.write_points(con, "I", "other_src", {D1: 0.5})
        path = tempfile.mktemp(suffix=".json")
        pathlib.Path(path).write_text(json.dumps({"points": {"M": {str(D1): 0.15, str(D2): 0.16}, "C": {}}}))
        dump.import_run(types.SimpleNamespace(import_from=path), con)
        got = con.execute("SELECT underlying, count(*) FROM iv_history GROUP BY 1 ORDER BY 1").fetchall()
        self.assertEqual(got, [("I", 1), ("M", 2)])     # the other source untouched
        pathlib.Path(path).unlink()


class TestDumpRun(unittest.TestCase):
    def test_each_product_is_written_as_soon_as_it_is_fetched(self):
        # A 2,500-request sweep that only writes at the end loses everything to one Ctrl-C.
        # (It did: a killed run left no file at all.) So the file must exist per product.
        seen = []

        def fake_fetch(args, ak, venue, prod, name, path, months, end, workers=4):
            seen.append(prod)
            if len(seen) == 2:
                # after the second product the file must already hold the first
                blob = json.loads(pathlib.Path(args.dump).read_text())
                self.assertIn(seen[0], blob["points"])
            return {D1: 0.2}, 1, {}

        args = types.SimpleNamespace(venues="DCE", products=["M", "C"], start="2025-01-02",
                                     end="2026-09-30", window=75, workers=1, limit_months=0,
                                     dump=tempfile.mktemp(suffix=".json"))
        with mock.patch.object(dump.product, "fetch_product", side_effect=fake_fetch), \
             mock.patch.object(dump.chains, "underlying_path", return_value={}), \
             mock.patch.object(dump.calendar, "month_codes", return_value=[]):
            dump.dump_run(args, ak=None, cal=[])
        blob = json.loads(pathlib.Path(args.dump).read_text())
        self.assertEqual(sorted(blob["points"]), ["C", "M"])
        pathlib.Path(args.dump).unlink()

    def test_unknown_product_for_a_venue_is_skipped_not_guessed(self):
        args = types.SimpleNamespace(venues="CFFEX", products=["M"], start="2025-01-02",
                                     end="2026-09-30", window=75, workers=1, limit_months=0,
                                     dump=tempfile.mktemp(suffix=".json"))
        with mock.patch.object(dump.chains, "underlying_path") as up:
            dump.dump_run(args, ak=None, cal=[])
        up.assert_not_called()
        # Nothing fetched means no file -- not an empty one that would import as "no points".
        self.assertFalse(pathlib.Path(args.dump).exists())


if __name__ == "__main__":
    unittest.main()
