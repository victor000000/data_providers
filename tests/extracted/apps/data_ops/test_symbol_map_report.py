"""test_symbol_map_report.py — the two reports, which answer different questions.

report_rows reads what we are ABOUT to write; report_db reads what is IN the table. The
gap between them is the repo's most-repeated bug -- a job that prints what it COLLECTED
rather than what it STORED reads as success either way -- so both are exercised.
"""
import io
import pathlib
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

import duckdb

from data_providers.jobs.data_ops.symbol_map_report import report_db, report_rows


def captured(fn, *a):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*a)
    return buf.getvalue()


ROWS = [
    ("MA", "ibkr", "MA", "equity", ""),
    ("CZCE:MA", "akshare", "MA", "cn_future", "the collision"),
    ("BRK B", "ibkr", "BRK B", "equity", ""),
    ("BRK B", "alpaca", "BRKB", "equity", ""),
]


class TestReportRows(unittest.TestCase):
    def test_names_both_traps(self):
        out = captured(report_rows, ROWS)
        # one spelling, two instruments
        self.assertIn("'MA'", out)
        self.assertIn("collisions", out)
        # one instrument, many spellings
        self.assertIn("multi-spelling", out)
        self.assertIn("BRK B", out)

    def test_rows_without_a_symbol_are_not_counted(self):
        out = captured(report_rows, ROWS + [("X", "yahoo", None, "equity", "")])
        self.assertNotIn("'None'", out)

    def test_an_empty_derivation_reports_zero_by_kind(self):
        out = captured(report_rows, [])
        self.assertIn("{}", out)


class TestReportDb(unittest.TestCase):
    def test_it_reads_the_table_not_the_rows(self):
        # The whole reason report_db exists: hand it a table MISSING a row that
        # report_rows would have shown, and it must report the table's truth.
        con = duckdb.connect(":memory:")
        con.execute("CREATE SCHEMA ref")
        con.execute("""CREATE TABLE ref.symbol_map (canonical TEXT, source TEXT, symbol TEXT,
                       kind TEXT, note TEXT)""")
        rows = ROWS[:1]
        if rows:   # DuckDB raises on an empty list, not a no-op
            con.executemany("INSERT INTO ref.symbol_map VALUES (?,?,?,?,?)", rows)
        out = captured(report_db, con)
        self.assertIn("equity", out)
        self.assertNotIn("cn_future", out, "that row is in ROWS, not in the table")
        con.close()

    def test_it_survives_an_empty_table(self):
        con = duckdb.connect(":memory:")
        con.execute("CREATE SCHEMA ref")
        con.execute("""CREATE TABLE ref.symbol_map (canonical TEXT, source TEXT, symbol TEXT,
                       kind TEXT, note TEXT)""")
        captured(report_db, con)          # must not raise
        con.close()


if __name__ == "__main__":
    unittest.main()
