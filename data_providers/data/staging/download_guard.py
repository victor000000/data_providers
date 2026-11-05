import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""download_guard.py — did a downloader fetch data we ALREADY had?

check_duplicates.py answers a different question: "are there duplicate KEYS in the merged
databases?" That can only ever be no, because every table has a primary key and the merge
is INSERT OR REPLACE. It proves the write path is sound, but it is structurally incapable
of noticing the thing we actually care about -- a downloader spending an hour and a rate
limit re-fetching rows that were already sitting in market_history.

This is that check. It compares each staging file against the merged database on the
table's declared PRIMARY KEY and reports overlap:

    0% overlap        the skip logic worked; this is genuinely new data
    high overlap      the skip logic failed and the run re-downloaded what we had

Run it BEFORE merging. A staging file with heavy overlap should not be merged blindly --
the rows are harmless (the PK absorbs them) but the run wasted the quota, and whatever
made the skip logic fail will do it again next time.

    uv run --no-project --with duckdb python scripts/download_guard.py
"""
import pathlib
import sys

import duckdb

from data_providers.data import db  # noqa: E402

ROOT = bootstrap.ROOT
HISTORY = ROOT / "market_history.duckdb"


def pk_of(con, table):
    """The declared PRIMARY KEY, or None. Read from duckdb_constraints rather than guessed,
    so a table whose key changes is tracked automatically instead of silently mis-joined."""
    try:
        rows = con.execute("""
            SELECT constraint_column_names FROM duckdb_constraints()
            WHERE table_name = ? AND constraint_type = 'PRIMARY KEY'""",
            [table]).fetchall()
        return list(rows[0][0]) if rows and rows[0][0] else None
    except Exception:
        return None


def main():
    staged = sorted((ROOT / "staging").glob("*.duckdb"))
    if not staged:
        print("no staging files — nothing in flight, nothing to guard")
        return 0

    total_overlap = 0
    checked = skipped = 0
    for sp in staged:
            try:
                sc = duckdb.connect(str(sp), read_only=True)
                db.tune(sc)   # same per-process profile as every db.connect() connection
            except Exception as e:
                # A staging file mid-download is normal, not an error. Report it as
                # unverifiable rather than crashing -- a guard that dies on the exact
                # situation it exists to inspect is useless.
                print(f"\n{sp.name}: busy ({type(e).__name__}) — a downloader is writing it; "
                      f"overlap cannot be judged yet.")
                skipped += 1
                continue
            # ATTACH the merged side ONTO THIS CONNECTION. The first version opened a
            # SEPARATE `live` connection and then wrote `FROM live."{t}"` in SQL still
            # executing on `sc` -- where `live` is not a catalog but an unknown schema.
            # That raised CatalogException, the handler below swallowed it, and the tool
            # printed "Safe to merge" on every file it was handed. A guard that reports
            # the opposite of the truth is worse than no guard: it was built to catch
            # re-downloading and instead certified 275,320 already-held rows as new.
            try:
                sc.execute(f"ATTACH '{HISTORY}' AS live (READ_ONLY)")
            except Exception as e:
                print(f"\n{sp.name}: market_history unavailable ({type(e).__name__}) — "
                      f"a writer holds it; overlap CANNOT be judged. Not reporting clean.")
                sc.close()
                skipped += 1
                continue
            try:
                tables = [r[0] for r in sc.execute(
                    "SELECT table_name FROM duckdb_tables()").fetchall()]
                print(f"\n{sp.name}")
                for t in tables:
                    if t.startswith("_snap_"):
                        continue          # the snapshot cache, not staged data
                    try:
                        n = sc.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                    except Exception as e:
                        # Count it as SKIPPED, not as nothing-to-do. `continue` alone
                        # left `skipped` untouched, so a table this guard could not
                        # read looked identical to a table it had verified as empty --
                        # and the verdict below ("all-new ... skip logic held") was
                        # printed on the strength of tables that were never examined.
                        print(f"  {t}: could not count ({repr(e)[:60]}) — not compared")
                        skipped += 1
                        continue
                    if not n:
                        continue
                    pk = pk_of(sc, t)
                    if not pk or not all(p in
                            {c[0] for c in sc.execute(f'DESCRIBE "{t}"').fetchall()}
                            for p in pk):
                        print(f"  {t:24} {n:>8,} rows — no readable PK, overlap unknown")
                        skipped += 1
                        continue
                    cols = ", ".join(f'"{c}"' for c in pk)
                    try:
                        hit = sc.execute(f"""
                            SELECT count(*) FROM "{t}" s
                            WHERE EXISTS (SELECT 1 FROM live."{t}" l
                                          WHERE {' AND '.join(
                                              f'l."{c}" IS NOT DISTINCT FROM s."{c}"'
                                              for c in pk)})""").fetchone()[0]
                    except Exception as e:
                        print(f"  {t:24} {n:>8,} rows — cannot compare ({str(e)[:40]})")
                        skipped += 1
                        continue
                    pct = 100.0 * hit / n
                    total_overlap += hit
                    checked += 1
                    flag = "RE-DOWNLOADED" if pct >= 50 else ("partial overlap" if hit else "all new")
                    print(f"  {t:24} {n:>8,} rows · {hit:>8,} already held "
                          f"({pct:5.1f}%) — {flag}")
            finally:
                sc.close()

    print()
    # The verdict must account for what was SKIPPED. The first version printed "Safe to
    # merge" whenever total_overlap was 0 -- including when every table had been skipped
    # and nothing was compared at all, which is the state a broken join produces. A clean
    # verdict now requires having actually checked something.
    if total_overlap:
        print(f"{total_overlap:,} staged rows already existed in market_history. The merge "
              f"will absorb them safely (PK), but that fetching was WASTED — the skip logic "
              f"missed them.")
        # Explicit, not a fall-through. This is a WARNING about wasted work, not a
        # failure: the rows are already held and the merge absorbs them on the PK, so
        # the file is safe to merge. Previously this branch printed and then fell out
        # of the if/elif chain to the trailing `return 0`, which read as accidental.
        return 0
    if checked == 0:
        print(f"NOTHING WAS COMPARED ({skipped} table(s) skipped). This is NOT a clean "
              f"result — treat the staging file as unverified.")
        return 1
    elif skipped:
        print(f"{checked} table(s) all-new, but {skipped} could not be compared. "
              f"Partly verified, not clean.")
        return 1
    else:
        print(f"No staged row duplicates anything already merged across {checked} "
              f"table(s) — the skip logic held and every fetched row is new. Safe to merge.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
