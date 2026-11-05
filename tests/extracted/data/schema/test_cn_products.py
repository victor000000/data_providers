"""test_cn_products.py — the CN option universe, as data.

The universe is seeded into `cn_product_map` and every CN builder reads it, so a
malformed row is not a cosmetic problem: it is a product that silently never gets an IV
series, or a canonical key that collides with another venue's product.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.data.schema.cn_products import PRODUCTS

# Sina's own option page (optionsDP.php), which is where akshare builds its symbol map
# from -- so this is the universe, not a guess. Products we hold that Sina does not list
# (FG/SA/UR/P/PP, INE:SC, the CFFEX index options) come from the venue endpoints instead.
SINA_COMMODITY = {"CZCE": {"CF", "SR", "TA", "OI", "PK", "MA", "RM", "ZC", "PX", "SH"},
                  "DCE": {"M", "C", "I", "PG", "A", "B", "Y", "EG", "EB"},
                  "SHFE": {"RU", "CU", "AU", "AG", "RB", "BR", "AL"},
                  "GFEX": {"SI", "LC"}}


class TestUniverse(unittest.TestCase):
    def test_no_duplicate_canonical_keys(self):
        # PRIMARY KEY (canonical) in cn_product_map -- a duplicate here means one of the
        # two products is silently dropped by INSERT OR REPLACE at seed time.
        keys = [p[0] for p in PRODUCTS]
        self.assertEqual(len(keys), len(set(keys)),
                         f"duplicate canonical(s): "
                         f"{sorted({k for k in keys if keys.count(k) > 1})}")

    def test_canonical_matches_venue_and_product(self):
        for canon, venue, prod, *_ in PRODUCTS:
            self.assertEqual(canon, f"CN:{venue}:{prod}")

    def test_every_ak_symbol_is_present(self):
        # An entry with an empty ak_symbol fetches nothing and reports success -- the
        # failure mode that cost every GFEX row and, separately, the INE Chinese name.
        for canon, _v, _p, ak, endpoint, _fmt, _cols in PRODUCTS:
            self.assertTrue(ak, f"{canon}: no ak_symbol")
            self.assertTrue(endpoint, f"{canon}: no endpoint")

    def test_the_eleven_sina_products_are_all_mapped(self):
        # Found 2026-10-07 by scraping Sina, and Y was missed by the FIRST pass at this
        # comparison because it read plans/symbol_map.csv, which mixes cn_product_map with
        # universe_futures_cn.txt. Asserted against the table, not that file.
        mapped = {(v, p) for _c, v, p, *_ in PRODUCTS}
        missing = [(v, p) for v, ps in SINA_COMMODITY.items() for p in ps
                   if (v, p) not in mapped]
        self.assertFalse(missing, f"Sina lists these and we do not map them: {missing}")

    def test_the_cn_universe_has_no_us_market_codes(self):
        # 'MA' is CZCE methanol AND NYSE Mastercard; resolving one as the other is the
        # collision this whole map exists to prevent. A CN entry must carry CN: keying.
        for canon, *_ in PRODUCTS:
            self.assertTrue(canon.startswith("CN:"), canon)


if __name__ == "__main__":
    unittest.main()
