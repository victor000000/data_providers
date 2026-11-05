import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""migrate_akshare_staging.py — move CN rows stranded in staging/akshare into cn_market.

Why they are stranded: download_cn_option_bars.py wrote to `staging="akshare"`, whose merge
target is market_history — US-only since the CN split. Its CZCE output (232 MB, TA's 169,033
rows among it) has been landing there ever since. The fetcher is now repointed at cn_market,
and merge_staging refuses to fold CN into market_history, so these rows have nowhere to go
without this.

TWO PHASES, TWO CONNECTIONS — the pattern that made db_split_cn.py work after its first
version aliased one connection for both roles, reported success, and copied nothing:

    phase 1  connect to cn_market, ATTACH staging/akshare READ-ONLY  -> copy
    phase 2  connect to staging/akshare, opened read-write           -> delete
             (only if phase 1 verified every table's row count exactly)

Columns align without rewriting: cn_market.option_bars' first 13 are byte-for-byte the
market_history order the old fetcher's positional INSERT targets (source, contract,
underlying, expiry, strike, call_put, day, open, high, low, close, volume, vwap). `venue` and
`product` arrive NULL and are backfilled from `source`, exactly as db_split_cn.py did.

    uv run python apps/data_ops/migrate_akshare_staging.py          # dry
    uv run python apps/data_ops/migrate_akshare_staging.py --apply
"""
import argparse
import pathlib

import duckdb

ROOT = bootstrap.ROOT
SRC = ROOT / "staging" / "akshare.duckdb"
DST = ROOT / "cn_market.duckdb"

TABLES = ["option_bars", "option_flow_daily", "iv_history"]
CN_PRED = "(source LIKE 'akshare%' OR source LIKE 'cn[_]%')"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not SRC.exists():
        print(f"  {SRC.name} does not exist — nothing stranded"); return

    # ── phase 1: copy, source attached read-only to the destination ──────────────────
    dst = duckdb.connect(str(DST))
    dst.execute(f"ATTACH '{SRC}' AS s (READ_ONLY)")
    present = {r[0] for r in dst.execute(
        "SELECT table_name FROM duckdb_tables() WHERE database_name='s'").fetchall()}

    verified = {}
    for t in TABLES:
        if t not in present:
            continue
        n = dst.execute(f"SELECT count(*) FROM s.{t} WHERE {CN_PRED}").fetchone()[0]
        if not n:
            print(f"  {t:20} no CN rows"); continue
        # Name the columns. cn_market's option_bars has 19 columns (venue, product, oi,
        # settle, iv, delta were ADDed); the staging file still has market_history's 13, so
        # `SELECT *` supplies 13 values to a 19-column table and DuckDB refuses. The 13 map
        # positionally, with the staging `symbol` landing in cn_market's `contract`.
        cols = [d[0] for d in dst.execute(f"DESCRIBE s.{t}").fetchall()]
        tgt = [d[0] for d in dst.execute(f"DESCRIBE {t}").fetchall()]
        if t == "option_bars":
            src_cols = "source, symbol, underlying, expiry, strike, call_put, day, open, high, low, close, volume, vwap"
            dst_cols = "source, contract, underlying, expiry, strike, call_put, day, open, high, low, close, volume, vwap"
        else:
            shared = [c for c in cols if c in tgt]
            src_cols = dst_cols = ", ".join(shared)
        # DELETE ONLY WHAT THIS RUN RE-INSERTS -- scoped to the sources staging actually
        # holds, never to all CN rows.
        #
        # The first version deleted `WHERE source LIKE 'akshare%'`, which is every CN row in
        # cn_market, then inserted only the staged ones. It wiped MA (152,304 rows), AL, AU,
        # CU, RU and ZN from the target while reporting success -- the exact
        # delete-broadly-then-insert-narrowly bug found in merge_staging earlier the same
        # day, reintroduced in a new file. A "make the re-run idempotent" delete must be
        # scoped to the same predicate as the insert, or it is a data-destroying delete
        # wearing an idempotency comment.
        _srcs = [r[0] for r in dst.execute(
            f"SELECT DISTINCT source FROM s.{t} WHERE {CN_PRED}").fetchall()]
        for _sc in _srcs:
            dst.execute(f"DELETE FROM {t} WHERE source = ?", [_sc])
        dst.execute(f"INSERT INTO {t} ({dst_cols}) SELECT {src_cols} FROM s.{t} "
                    f"WHERE {CN_PRED} ON CONFLICT DO NOTHING")
        got = dst.execute(f"SELECT count(*) FROM {t} WHERE {CN_PRED}").fetchone()[0]
        verified[t] = (n, got)
        print(f"  {t:20} {n:>9,} stranded CN rows -> "
              f"{'OK' if got == n else f'MISMATCH {got} != {n}'}")

    # venue/product were NULL on insert (the old fetcher's schema had no such columns);
    # backfill from `source`, which encodes both: akshare_czce_ta -> CZCE, TA.
    dst.execute(f"""UPDATE option_bars SET
                      venue   = COALESCE(venue,   upper(split_part(source,'_',2))),
                      product = COALESCE(product, upper(split_part(source,'_',3)))
                    WHERE {CN_PRED} AND venue IS NULL""")
    dst.execute("DETACH s")
    dst.close()

    bad = [t for t, (n, g) in verified.items() if n != g]
    total = sum(g for _, g in verified.values())
    print(f"\n  {total:,} rows now in {DST.name}")
    if bad:
        print(f"  ABORTING phase 2 — did not verify: {bad}"); return

    if not args.apply:
        print("  DRY RUN — re-run with --apply to clear staging/akshare"); return
    src = duckdb.connect(str(SRC))
    for t in verified:
        n = src.execute(f"SELECT count(*) FROM {t} WHERE {CN_PRED}").fetchone()[0]
        if n:
            src.execute(f"DELETE FROM {t} WHERE {CN_PRED}")
            print(f"  cleared {n:>9,} rows from staging/akshare.{t}")
    src.close()
    print("  staging/akshare is empty; CN lives in cn_market only")


if __name__ == "__main__":
    main()
