import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""merge_staging.py — fold per-source staging databases into market_history.

Staging exists so the sources can download CONCURRENTLY: Alpaca, Futu, TWS and akshare have
independent rate limits, but DuckDB allows one writer per file, so without staging every
downloader queued behind the same lock. Each source writes staging/<source>.duckdb (attached
as `mh`, so the writers needed no other change); this folds them in, one writer at a time.

Idempotent: every table is copied with INSERT ... ON CONFLICT DO NOTHING against the same
primary keys the writers used, so merging twice changes nothing. A staging file is deleted
only after its rows verify in the target.

    uv run --no-project --with duckdb python scripts/merge_staging.py [--keep]
"""
import argparse
import pathlib

import duckdb

from data_providers.data.db import tune

ROOT = bootstrap.ROOT
# tables whose writers delete-then-insert for a symbol/day: staging is the newer truth for
# whatever it covers, so those target rows must go before the insert
REPLACE_SEMANTICS = {"iv_history": ("source",), "underlying_daily": ("source",),
                     "option_flow_daily": ("source",)}

TABLES = ["option_bars", "option_greeks_daily", "option_oi_daily", "option_bars_checks", "option_minutes", "underlying_minutes",
          "underlying_daily", "iv_history", "iv_history_checks", "option_flow_daily", "fundamentals_snapshot"]

# Checks tables are memories, not series: "we asked, here is what came back". A plain
# ON CONFLICT DO NOTHING would freeze the first answer ever merged — so a later pass that
# reaches further back, or finds bars where there were none, would be silently discarded and
# the downloader would keep re-asking a question the target already answered. Upsert instead,
# widening the recorded window rather than replacing it.
UPSERT = {
    "iv_history_checks": """ON CONFLICT (source, underlying) DO UPDATE SET
        oldest_seen = LEAST(COALESCE(iv_history_checks.oldest_seen, EXCLUDED.oldest_seen),
                            COALESCE(EXCLUDED.oldest_seen, iv_history_checks.oldest_seen)),
        newest_seen = GREATEST(COALESCE(iv_history_checks.newest_seen, EXCLUDED.newest_seen),
                               COALESCE(EXCLUDED.newest_seen, iv_history_checks.newest_seen)),
        bars = EXCLUDED.bars, checked_utc = EXCLUDED.checked_utc""",
    "option_bars_checks": """ON CONFLICT (source, underlying, expiry) DO UPDATE SET
        bars = EXCLUDED.bars, checked_utc = EXCLUDED.checked_utc""",
}

ap = argparse.ArgumentParser()
ap.add_argument("--keep", action="store_true", help="keep the staging file after merging")
ap.add_argument("--files", nargs="+", help="Merge only these staging filenames, retaining other stages")

# Parsed against the REAL argv only when this module IS the program.
#
# `parse_args()` reads the CALLER's sys.argv, so at module level it makes importing this
# module a function of whatever flags the importer was given: `import merge_staging` from a
# script called with any flag this parser does not know raises SystemExit and kills that
# process, with an argparse usage message naming the wrong program. Nothing imports it today
# — the scripts/ shim aliases it — so this was latent, not live. It was found by
# scripts/check_all.sh, which passes an argument and imported this module as one of 187.
#
# The shim still works: it runs the module with run_name="__main__", so the real argv is
# parsed exactly as before. `args` is read once (the --keep check far below), so an imported
# module simply sees the defaults.
args = ap.parse_args() if __name__ == "__main__" else ap.parse_args([])

# ── CN must never be folded back into market_history ─────────────────────────
# market_history is US-ONLY since the CN split (2026-10-01). But download_cn_option_bars.py
# and build_iv_history_cn.py still write to `staging="akshare"`, whose merge target is
# market_history — so folding that file would quietly undo the split and put methanol back
# beside Mastercard in the table every US query reads. The CN fetchers need repointing at
# cn_market; until then this refuses the merge rather than performing a destructive one.
CN_SOURCES = ("akshare%", "cn[_]%")

# Tables that threw for the file CURRENTLY being merged. Deliberately reset per file --
# as a single process-global list, one failure in the first staging file made every later
# file "fail" too: each printed a warning naming tables it may not even contain, and each
# was kept forever, so the lifecycle never completed and the message pointed at the wrong
# file. `any_failed` carries the across-files fact to the exit code.
any_failed = []             # [(file, table)] over the whole run

# ── Pre-merge sanity: purge option_bars rows that cannot be true ─────────────
# The merge is the only place with read-write access to market_history (every downloader
# holds it read-only as `mhro` for its whole run), so repairs that must touch merged data
# belong here. This one is owed from the CZCE bug: download_cn_option_bars parsed CZCE
# YMM codes as YYMM, so `MA402` (2024-02) became 2004-02 and 152,304 rows went into
# market_history with expiries 2004-2007 against 2024-2026 trading days. The parser is
# fixed, but option_bars merges ON CONFLICT DO NOTHING — a correct re-fetch would sit
# ALONGSIDE the corrupt rows instead of replacing them, so they have to be deleted
# explicitly. An expiry before the observation day is impossible; so is one beyond five
# years, which is longer than any listed option.
def purge_impossible_expiries(con):
    """Purge impossible option_bars expiries. Returns True if the repair could not run."""
    try:
        n = con.execute("""SELECT count(*) FROM option_bars
                           WHERE expiry < day OR expiry > day + INTERVAL 5 YEAR""").fetchone()[0]
        if n:
            con.execute("""DELETE FROM option_bars
                           WHERE expiry < day OR expiry > day + INTERVAL 5 YEAR""")
            print(f"  PURGED {n:,} option_bars rows with impossible expiries "
                  f"(expiry < day, or > 5y out) — they will be re-fetched correctly")
        else:
            print("  option_bars expiry sanity: clean")
        return False                     # ran — the repair is in place
    except Exception as e:
        # NOT a harmless skip. option_bars merges with ON CONFLICT DO NOTHING, so the
        # corrupt rows this purge exists to remove share a PK with their corrected
        # re-fetches -- the fix is silently dropped by the conflict clause and the
        # staging file is then unlinked, destroying the only copy of it. Flag it so the
        # caller keeps the file.
        print(f"  expiry purge SKIPPED: {repr(e)[:70]}")
        print("  !! the purge is what lets corrected option_bars replace corrupt ones; "
              "with it skipped the re-fetch is discarded by ON CONFLICT DO NOTHING")
        # MUST return True. This fell through to an implicit None when the function
        # was first written, and `if failed_tables or purge_failed` treats None as
        # falsy -- so the "KEPT" branch was skipped and the staging file was DELETED
        # at the bottom of the loop, destroying the only copy of the corrected rows.
        # That is the exact data-loss path this function exists to prevent.
        return True



def main():
    staged = sorted((ROOT / "staging").glob("*.duckdb")) if (ROOT / "staging").exists() else []
    selected = getattr(args, "files", None)
    if selected:
        if any(pathlib.Path(name).name != name or not name.endswith('.duckdb') for name in selected):
            raise ValueError('Use staging filenames only')
        missing = set(selected) - {path.name for path in staged}
        if missing:
            raise FileNotFoundError('Missing requested staging files: '+', '.join(sorted(missing)))
        staged = [path for path in staged if path.name in selected]
    if not staged:
        print("nothing staged"); return 0

    con = duckdb.connect(str(ROOT / "market_history.duckdb"))
    # Same per-process resource profile every db.connect() connection gets (threads=4, 3GB
    # memory cap, 256MB checkpoints, no insertion-order buffering): the merge is a bulk job
    # that opens DuckDB directly, and it must not run at the 16-thread / 80%-of-RAM defaults
    # while downloaders are alive.
    tune(con)
    import sys

    from data_providers.data.schema import history as db_init_history   # renamed from db_init_history; keep the
    # local name so the calls below (`db_init_history.ensure(...)`) are unchanged.
    purge_failed = purge_impossible_expiries(con)
    # prefix="" — this connection IS market_history (attached as the main schema, no alias), so
    # the tables belong at the top level. The default prefix is "mh.", which names the STAGING
    # attachment elsewhere; using it here raised "Schema with name mh does not exist" and killed
    # the merge before it folded a single table, silently leaving every staging file unmerged
    # (39 MB in staging/alpaca_bars.duckdb was the visible symptom).
    db_init_history.ensure(con, prefix="")
    for path in staged:
        print(f"merging {path.name}")
        failed_tables = []      # per file, reset here — see the note at `any_failed`
        # A live job holds an exclusive lock, so ATTACH fails. Skip it rather than abort the
        # whole merge: the other files are independent, and this one merges on the next run.
        # Without this guard a single running downloader stopped every other file from folding.
        try:
            con.execute(f"ATTACH '{path}' AS st")
        except Exception as e:
            print(f"  {path.name:22} SKIPPED — in use by a writer ({repr(e)[:60]})")
            continue
        for tab in TABLES:
            try:
                exists = con.execute("SELECT count(*) FROM duckdb_tables() WHERE database_name='st' "
                                     "AND table_name=?", [tab]).fetchone()[0]
                if not exists:
                    continue
                n_st = con.execute(f"SELECT count(*) FROM st.{tab}").fetchone()[0]
                if not n_st:
                    continue
                before = con.execute(f"SELECT count(*) FROM {tab}").fetchone()[0]
                cn = con.execute(
                    f"SELECT count(*) FROM st.{tab} WHERE " +
                    " OR ".join(f"source LIKE '{p}'" for p in CN_SOURCES)).fetchone()[0] \
                    if any(c.lower() == "source" for c in
                           [d[0] for d in con.execute(f"DESCRIBE {tab}").fetchall()]) else 0
                if cn:
                    raise RuntimeError(
                        f"{cn:,} CN rows in {tab} would be merged into market_history, which is "
                        f"US-only — repoint the CN fetcher at cn_market instead")
                # Refresh tables UPSERT; they do NOT delete-then-insert.
                #
                # The old code deleted every target row for a source present in staging and then
                # inserted staging's rows. That silently destroys merged history, because staging
                # holds only the COMPLEMENT of the target by design: downloaders skip what they
                # already have, and the merge unlinks the staging file afterwards, so each run
                # stages just the gap. Merge #1 folds A..BOE; run 2 stages only the tail; merge #2
                # deletes A..BOE and inserts the tail — the data is gone. It was latent only
                # because market_history happened to be locked all day.
                #
                # INSERT OR REPLACE keeps the delete half's intent (a re-fetch still overwrites
                # its own rows, so refresh semantics hold) without the collateral damage: rows the
                # target holds that staging does not mention are left untouched.
                if tab in REPLACE_SEMANTICS:
                    con.execute(f"INSERT OR REPLACE INTO {tab} SELECT * FROM st.{tab}")
                    print(f"  {tab:22} upserted (refresh semantics, no delete)")
                else:
                    conflict = UPSERT.get(tab, "ON CONFLICT DO NOTHING")
                    con.execute(f"INSERT INTO {tab} SELECT * FROM st.{tab} {conflict}")
                after = con.execute(f"SELECT count(*) FROM {tab}").fetchone()[0]
                print(f"  {tab:22} {n_st:>9,} staged -> +{after-before:,} new rows ({after:,} total)")
            except Exception as e:
                print(f"  {tab:22} !! {repr(e)[:80]}")
                failed_tables.append(tab)

        # ── Tables in the FILE that TABLES does not name. ────────────────────────────────
        # The loop above iterates the hardcoded TABLES list, so a table a writer staged
        # under a new name was never examined at all -- and the unlink below then deleted
        # the staging file, destroying the rows with no message. That is the same shape as
        # the incident this module's docstring is about: a delete whose scope exceeds its
        # insert. Any unrecognised table now keeps the file.
        #
        # `_snap_*` is the ONE exception, and it is not data: when a downloader cannot attach
        # market_history read-only it copies the tables it needs into its own staging file to
        # answer "what do we already hold?" (data/db.py). Those copies are reader scratch, and
        # leaving them in scope meant no staged file could ever be merged again -- measured
        # 2026-10-07, when an 844 MB Alpaca staging file holding 278 tickers was kept back by
        # 27M rows of market_history's own reflection.
        try:
            staged_tabs = {r[0] for r in con.execute(
                "SELECT table_name FROM duckdb_tables() WHERE database_name='st'").fetchall()}
        except Exception as e:
            staged_tabs = set()
            failed_tables.append(f"<cannot list tables: {repr(e)[:40]}>")
        unknown = sorted(t for t in staged_tabs - set(TABLES) if not t.startswith("_snap_"))
        if unknown:
            for t in unknown:
                n = con.execute(f"SELECT count(*) FROM st.{t}").fetchone()[0]
                print(f"  {t:22} !! NOT IN THE MERGE LIST ({n:,} rows) — not merged")
            failed_tables.append("unlisted:" + ",".join(unknown))

        con.execute("DETACH st")
        # Delete the staging file ONLY if every table folded. This module's own docstring
        # promises exactly that, and the code did the opposite: a failure printed "!!", the loop
        # moved on, and the file was unlinked anyway — destroying the only copy of the rows that
        # had just failed to merge. Kept on failure so the next run retries them.
        if failed_tables or purge_failed:
            why = ", ".join(failed_tables) if failed_tables else "option_bars expiry purge skipped"
            print(f"  {path.name} KEPT — {why}. Fix and re-run; the rows are still here.")
            any_failed.extend((path.name, t) for t in (failed_tables or ["<purge>"]))
        elif not args.keep:
            path.unlink()
            print(f"  {path.name} removed (merged)")
    # ── Verify no duplicates before declaring the merge good ─────────────────────
    # The merge is where new data lands, so it is the only place a duplicate can be
    # introduced -- and a duplicate row is indistinguishable from the row it copies:
    # every count, average and IVR built on it is quietly wrong. Running this here means
    # the question "did we duplicate download?" is answered by a query at the moment the
    # data arrives, rather than by confidence later.
    # Release the target FIRST: check_duplicates opens each DB read_only, and DuckDB refuses
    # that while this process still holds market_history.duckdb for writing. With the check
    # above the close, the very file the merge just wrote was always reported
    # "SKIPPED — held by a writer", and its last line -- the one parsed below -- became
    # "no duplicate keys anywhere": a clean bill of health for the one file that was not read.
    con.close()

    # Exit codes: 0 verified clean · 1 duplicates found · 2 could not verify.
    # A guard that cannot fail is not a guard. This used to print a warning and fall off the
    # end of the script with status 0, so a merge that duplicated rows -- or a checker that
    # never ran at all -- reported success either way, and the supervisor gating on the exit
    # code saw a clean run.
    _verify_rc = 0
    try:
        import subprocess, sys as _sys
        _r = subprocess.run([_sys.executable, "-m", "data_providers.jobs.audit.check_duplicates"],
                            capture_output=True, text=True, timeout=300)
        print(_r.stdout.strip().splitlines()[-1] if _r.stdout.strip() else
              "  duplicate check produced no output")
        if _r.returncode:
            print("  !! DUPLICATES PRESENT — the merge landed data that is wrong")
            _verify_rc = 1
    except Exception as e:
        print(f"  !! duplicate check COULD NOT RUN: {repr(e)[:60]}")
        print("     unverified is not the same as clean -- reporting failure, not success")
        _verify_rc = 2

    # A merge that kept staging files did NOT fully succeed: rows are sitting unmerged, and
    # the supervisor gating on this exit code must not read that as clean. Reported as 2
    # (could not fully verify) rather than 1 (duplicates) -- the data is not wrong, it is
    # incomplete, and the two want different responses.
    if any_failed:
        print(f"\n{len({f for f, _ in any_failed})} staging file(s) NOT fully merged "
              f"({len(any_failed)} table issue(s)). Their rows are still on disk.")
        make_fail = 2
    else:
        make_fail = 0

    if _verify_rc or make_fail:
        raise SystemExit(_verify_rc or make_fail)


# Guard added by the layer restructure. Without it, IMPORTING this module ran the
# merge against live staging files -- and with an empty staging/ it raised
# SystemExit, killing the importer. check_all.sh already had to special-case it.
if __name__ == "__main__":
    raise SystemExit(main())
