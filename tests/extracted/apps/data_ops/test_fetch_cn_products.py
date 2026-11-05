"""test_fetch_cn_products.py — contract-code parsing, the step that silently drops rows.

`parse_code` returns None for anything it cannot read, and the caller treats None as "skip
this row" -- so a parser that fails on a whole venue's format does not raise, it stores
nothing and reports success. That is exactly what happened to GFEX (see the regression test).
"""
import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from data_providers.jobs.data_ops.fetch_cn_products import parse_code  # noqa: E402


class TestFormatsPerVenue(unittest.TestCase):
    def test_czce_is_ymm(self):
        # 'MA611' = MA, 2026-11 delivery, not 2011 and not a 4-digit year.
        root, exp, cp, k = parse_code("MA611C3000", "YMM", "CZCE")
        self.assertEqual((root, cp, k), ("MA", "C", 3000.0))
        self.assertEqual((exp.year, exp.month), (2026, 10))   # expires the month BEFORE

    def test_shfe_and_dce_are_yymm(self):
        for code, root in (("cu2611C80000", "CU"), ("m2601C3000", "M"), ("i2605P800", "I")):
            got = parse_code(code, "YYMM", "SHFE")
            self.assertIsNotNone(got, code)
            self.assertEqual(got[0], root)

    def test_gfex_separates_every_field(self):
        # REGRESSION: GFEX writes 'si2611-C-10000'. The parser used to read the character
        # after the month as the call/put flag, read '-', and return None -- so every one of
        # GFEX's 1611 lc2611 rows was dropped while the fetch reported success.
        root, exp, cp, k = parse_code("si2611-C-10000", "YYMM", "GFEX")
        self.assertEqual((root, cp, k), ("SI", "C", 10000.0))
        self.assertEqual(exp.month, 10, "si2611 delivers in November; it expires in October")
        root, _exp, cp, k = parse_code("lc2611-P-100000", "YYMM", "GFEX")
        self.assertEqual((root, cp, k), ("LC", "P", 100000.0))

    def test_every_product_in_the_map_has_a_parseable_example(self):
        # One real contract per venue, in the shape that venue's endpoint returns.
        examples = [("SR611C5000", "YMM", "CZCE"), ("sc2612C500", "YYMM", "INE"),
                    ("au2612C600", "YYMM", "SHFE"), ("m2601C3000", "YYMM", "DCE"),
                    ("si2611-C-10000", "YYMM", "GFEX"), ("mo2610C6000", "YYMM", "CFFEX")]
        for code, fmt, venue in examples:
            self.assertIsNotNone(parse_code(code, fmt, venue), f"{venue} {code}")

    def test_junk_still_returns_none(self):
        for bad in ("", "garbage", "2601", "m2601X3000", "m2601C"):
            self.assertIsNone(parse_code(bad, "YYMM", "DCE"), bad)


class TestExpiryWiring(unittest.TestCase):
    def test_the_parsed_expiry_comes_from_the_venue_rule(self):
        # cu2611 (Nov delivery) expires on the 5th-to-last trading day of October, NOT on
        # the 15th as the old rule had it -- a 7-11 day error that inflates every greek.
        _root, exp, _cp, _k = parse_code("cu2611C80000", "YYMM", "SHFE")
        self.assertGreater(exp, dt.date(2026, 10, 15))
        self.assertEqual(exp.month, 10)

    def test_a_strike_with_decimals_does_not_break_the_parse(self):
        # Some venues quote fractional strikes; float() must see '3500.5', not '3500.5C'.
        got = parse_code("m2601C3500.5", "YYMM", "DCE")
        self.assertEqual(got[3], 3500.5)


if __name__ == "__main__":
    unittest.main()
