"""symbol_map_report.py — the symbol map's two reports, split out of build_symbol_map.py.

Split because that file sat exactly on its 235-line lid and the fix it needed was a few
lines. The two functions belong apart anyway, because they answer DIFFERENT questions and
the gap between them is this repo's most-repeated bug:

    report_rows(rows)   reports what we are ABOUT to write
    report_db(con)      reports what is IN the table

A job that prints what it COLLECTED rather than what it STORED reads as success either way:
cn_iv/bars.py collected 15,748 bar rows, printed the number, and wrote none of them, and the
log looked identical to a good run. Keeping both reports side by side is what makes the
difference visible at the moment the map is built.
"""
from collections import defaultdict

HEADER = "\ncollisions — one string, two instruments (the trap):"
SPELLINGS = "\nmulti-spelling instruments (what a map is FOR):"


def report_rows(rows):
    """The derivation: what the universe files and cn_product_map produced."""
    kinds = defaultdict(int)
    bysym, bycanon = defaultdict(set), defaultdict(list)
    for canon, src, sym, kind, _note in rows:
        if not sym:
            continue
        kinds[kind] += 1
        bysym[sym].add(canon)                  # one spelling, two instruments
        bycanon[canon].append(f"{src}={sym}")  # one instrument, many spellings
    print("  by kind:", dict(kinds))
    print(HEADER)
    for k, v in list({k: v for k, v in bysym.items() if len(v) > 1}.items())[:6]:
        print(f"  {k!r}: {' vs '.join(sorted(v))}")
    print(SPELLINGS)
    for k, v in list(bycanon.items()):
        if len(v) > 1:
            print(f"  {k:<16} {' · '.join(sorted(v))}")


def report_db(con):
    """The table itself. Read back AFTER the insert, so a silent zero-row write shows."""
    for r in con.execute("""SELECT kind, count(*) FROM ref.symbol_map GROUP BY kind
                            ORDER BY 2 DESC""").fetchall():
        print(f"  {r[0]:<12} {r[1]:>5}")
    print(HEADER)
    for r in con.execute("""
            SELECT symbol, count(DISTINCT canonical), string_agg(DISTINCT canonical, ' vs ')
            FROM ref.symbol_map GROUP BY symbol HAVING count(DISTINCT canonical) > 1""").fetchall():
        print(f"  {r[0]!r}: {r[2]}")
    print(SPELLINGS)
    for r in con.execute("""
            SELECT canonical, string_agg(source || '=' || symbol, ' · ' ORDER BY source)
            FROM ref.symbol_map
            GROUP BY canonical HAVING count(*) > 1 ORDER BY canonical LIMIT 6""").fetchall():
        print(f"  {r[0]:<14} {r[1]}")
