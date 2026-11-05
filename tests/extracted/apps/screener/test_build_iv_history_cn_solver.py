"""test_build_iv_history_cn_solver.py — the import path, which must work with no network.

The fetch is the part that needs akshare; writing a dump file into the database must not, or
an --import during a feed outage fails for a reason that has nothing to do with it.
"""
import datetime as dt
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

import duckdb  # noqa: E402

from data_providers.jobs.history import build_iv_history_cn_solver as entry  # noqa: E402
from data_providers.jobs.history.cn_iv.dump import SOLVER_NAMES  # noqa: E402


def temp_db():
    path = tempfile.mktemp(suffix=".duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE TABLE iv_history (day DATE, underlying VARCHAR, expiry DATE, "
                "atm_iv DOUBLE, spot DOUBLE, source VARCHAR)")
    con.execute("CREATE TABLE futures_daily (product VARCHAR, day DATE, close DOUBLE, source VARCHAR)")
    con.close()
    return path


class TestImportPath(unittest.TestCase):
    def test_import_writes_a_dump_without_reaching_for_the_network(self):
        db = temp_db()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"points": {"M": {"2026-09-30": 0.154, "2026-09-29": 0.149}}}, f)
            dump = f.name
        # The point of the assertion: akshare is not imported at all on this path, so a
        # missing or blocked module cannot break an import.
        with mock.patch.dict(sys.modules, {"akshare": None}), \
             mock.patch.object(sys, "argv", ["x", "--import", dump, "--db", db]):
            entry.main()
        con = duckdb.connect(db, read_only=True)
        rows = con.execute("SELECT underlying, source, count(*) FROM iv_history "
                           "GROUP BY 1, 2").fetchall()
        con.close()
        self.assertEqual(rows, [("M", "akshare_m_solver", 2)])
        for p in (db, dump):
            pathlib.Path(p).unlink()

    def test_import_is_idempotent(self):
        db = temp_db()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"points": {"M": {"2026-09-30": 0.154}}}, f)
            dump = f.name
        for _ in range(2):
            with mock.patch.object(sys, "argv", ["x", "--import", dump, "--db", db]):
                entry.main()
        con = duckdb.connect(db, read_only=True)
        n = con.execute("SELECT count(*) FROM iv_history").fetchone()[0]
        con.close()
        self.assertEqual(n, 1, "a second import must replace, not append")
        for p in (db, dump):
            pathlib.Path(p).unlink()


class TestSolverNames(unittest.TestCase):
    def test_the_static_map_matches_cn_product_map(self):
        # --dump fetches without opening the database, so this map is the only product list
        # it has -- and a product missing from it is fetched never.
        self.assertEqual(sorted(SOLVER_NAMES["DCE"]),
                         ["C", "I", "M", "PG", "PP"])
        self.assertEqual(sorted(SOLVER_NAMES["CFFEX"]), ["MO"])
        self.assertEqual(len(set(SOLVER_NAMES["DCE"].values())), 5)

    def test_dce_names_are_the_variety_names_the_endpoint_expects(self):
        for prod, name in SOLVER_NAMES["DCE"].items():
            self.assertTrue(name.endswith("期权"), f"{prod}: {name}")


if __name__ == "__main__":
    unittest.main()
