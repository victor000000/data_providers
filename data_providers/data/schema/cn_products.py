"""cn_products.py — the CN option universe: one row per (venue, product).

Split out of cn.py, which is the schema; this is the DATA the schema is seeded with.
Kept as a plain list of tuples so it reads as a table. The universe is not hand-guessed:
it was reconciled against Sina's own product page (optionsDP.php), which is where
akshare builds its symbol map from -- see the note above the 2026-10-07 additions.
"""

# venue -> (code format, price columns, endpoint, how akshare names the product)
PRODUCTS = [
    # canonical,        venue,  product, ak_symbol,     endpoint,              code,  cols
    ("CN:CZCE:MA", "CZCE", "MA", "MA", "option_hist_yearly_czce", "YMM", "今开盘/今收盘"),
    ("CN:CZCE:TA", "CZCE", "TA", "TA", "option_hist_yearly_czce", "YMM", "今开盘/今收盘"),
    ("CN:CZCE:SR", "CZCE", "SR", "SR", "option_hist_yearly_czce", "YMM", "今开盘/今收盘"),
    ("CN:CZCE:CF", "CZCE", "CF", "CF", "option_hist_yearly_czce", "YMM", "今开盘/今收盘"),
    ("CN:CZCE:RM", "CZCE", "RM", "RM", "option_hist_yearly_czce", "YMM", "今开盘/今收盘"),
    ("CN:CZCE:OI", "CZCE", "OI", "OI", "option_hist_yearly_czce", "YMM", "今开盘/今收盘"),
    ("CN:CZCE:FG", "CZCE", "FG", "FG", "option_hist_yearly_czce", "YMM", "今开盘/今收盘"),
    ("CN:CZCE:SA", "CZCE", "SA", "SA", "option_hist_yearly_czce", "YMM", "今开盘/今收盘"),
    ("CN:CZCE:UR", "CZCE", "UR", "UR", "option_hist_yearly_czce", "YMM", "今开盘/今收盘"),
    ("CN:INE:SC", "INE", "SC", "原油期权", "option_hist_shfe", "YYMM", "开盘价/收盘价"),
    ("CN:SHFE:CU", "SHFE", "CU", "铜期权", "option_hist_shfe", "YYMM", "开盘价/收盘价"),
    ("CN:SHFE:RU", "SHFE", "RU", "天胶期权", "option_hist_shfe", "YYMM", "开盘价/收盘价"),
    ("CN:SHFE:AU", "SHFE", "AU", "黄金期权", "option_hist_shfe", "YYMM", "开盘价/收盘价"),
    ("CN:SHFE:AL", "SHFE", "AL", "铝期权", "option_hist_shfe", "YYMM", "开盘价/收盘价"),
    ("CN:SHFE:ZN", "SHFE", "ZN", "锌期权", "option_hist_shfe", "YYMM", "开盘价/收盘价"),
    ("CN:DCE:I", "DCE", "I", "铁矿石期权", "option_hist_dce", "YYMM", ""),
    ("CN:DCE:M", "DCE", "M", "豆粕期权", "option_hist_dce", "YYMM", ""),
    ("CN:DCE:C", "DCE", "C", "玉米期权", "option_hist_dce", "YYMM", ""),
    ("CN:DCE:PG", "DCE", "PG", "液化石油气期权", "option_hist_dce", "YYMM", ""),
    ("CN:DCE:PP", "DCE", "PP", "聚丙烯期权", "option_hist_dce", "YYMM", ""),
    ("CN:GFEX:SI", "GFEX", "SI", "工业硅", "option_hist_gfex", "YYMM", ""),
    ("CN:GFEX:LC", "GFEX", "LC", "碳酸锂", "option_hist_gfex", "YYMM", ""),
    ("CN:CFFEX:MO", "CFFEX", "MO", "mo", "option_cffex_zz1000_daily_sina", "YYMM", ""),
    # ── the 11 Sina lists and we did not map ────────────────────────────────────────────
    # Found 2026-10-07 by scraping Sina's own product page (optionsDP.php), which is where
    # akshare builds its symbol map from -- so it is the universe, not a guess. Our 27 came
    # from an earlier pass and simply never included these; 白银/螺纹钢/乙二醇 in particular
    # are heavily traded and were missing from every screen.
    # Every name below was verified to RESOLVE before being added: an entry that returns
    # nothing is indistinguishable from a product with no data, which is the failure this
    # repo keeps re-learning.
    ("CN:CZCE:PK", "CZCE", "PK", "PK", "option_hist_yearly_czce", "YMM", "今开盘/今收盘"),
    ("CN:CZCE:ZC", "CZCE", "ZC", "ZC", "option_hist_yearly_czce", "YMM", "今开盘/今收盘"),
    ("CN:CZCE:PX", "CZCE", "PX", "PX", "option_hist_yearly_czce", "YMM", "今开盘/今收盘"),
    ("CN:CZCE:SH", "CZCE", "SH", "SH", "option_hist_yearly_czce", "YMM", "今开盘/今收盘"),
    # SHFE quotes the option by Chinese NAME; the product code returns an EMPTY frame.
    ("CN:SHFE:AG", "SHFE", "AG", "白银期权", "option_hist_shfe", "YYMM", "开盘价/收盘价"),
    ("CN:SHFE:RB", "SHFE", "RB", "螺纹钢期权", "option_hist_shfe", "YYMM", "开盘价/收盘价"),
    ("CN:SHFE:BR", "SHFE", "BR", "丁二烯橡胶期权", "option_hist_shfe", "YYMM", "开盘价/收盘价"),
    # DCE's own endpoint answers HTTP 412, so these ride the Sina solver path like M/C/I.
    ("CN:DCE:A", "DCE", "A", "黄大豆1号期权", "option_hist_dce", "YYMM", ""),
    ("CN:DCE:B", "DCE", "B", "黄大豆2号期权", "option_hist_dce", "YYMM", ""),
    ("CN:DCE:EG", "DCE", "EG", "乙二醇期权", "option_hist_dce", "YYMM", ""),
    ("CN:DCE:EB", "DCE", "EB", "苯乙烯期权", "option_hist_dce", "YYMM", ""),
    # Y was missed by the first pass, and the miss is worth recording: the comparison that
    # found the other 11 read plans/symbol_map.csv, which MIXES cn_product_map with
    # universe_futures_cn.txt -- so a product listed only in the universe file looked
    # already-mapped. 豆油期权 resolves on Sina (50 strikes); 棕榈油期权 raises KeyError and
    # genuinely has no Sina options, which is why P is absent rather than overlooked.
    ("CN:DCE:Y", "DCE", "Y", "豆油期权", "option_hist_dce", "YYMM", ""),
]
