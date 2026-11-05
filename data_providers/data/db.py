import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""db.py — the one place that knows where data lives. Use it in every script.

Three databases, split by LIFECYCLE (not by asset class — an option and its
underlying share the same writers, the same joins, and the same refresh):

  trades.duckdb        the log of record — what WE did and captured.
                       fills · orders · account_daily · journal_entries
                       chain_snapshots · chain_rows (trade-time captures)
                       positions · risk_profile · screener_runs
                       Written continuously, tiny enough to back up daily.
                       NOTHING bulk ever writes here, so a fill can be recorded
                       while a long download runs in another file.

  market_history.duckdb  bulk, re-fetchable market data.
                       option_bars · iv_history · underlying_daily · underlying_minutes
                       Written by long-running downloaders; safe to rebuild.

  reference.duckdb     the structural layer — what things ARE (instruments,
                       option contracts, expiries, futures specs, calendars).
                       Small, slow-changing, read by everything; its own file so no
                       bulk job can ever lock it away from a live session.

  market_data/yahoo.duckdb  the subproject warehouse (US universe + 1980→ history).
                       READ-ONLY from here, always.

Aliases when attached: `mh` = market_history, `ref` = reference, `yh` = the universe warehouse.
The trading DB stays the default catalog, so unqualified names keep meaning
"the log" and market tables are always written `mh.<table>` / `yh.<table>`.

    con = db.connect(log="rw", history="ro", universe=True)   # e.g. a plan report
    con = db.connect(log=None, history="rw")                  # e.g. a bulk downloader
"""
import os
import pathlib

import duckdb

ROOT = bootstrap.ROOT
TRADES = ROOT / "trades.duckdb"
HISTORY = ROOT / "market_history.duckdb"
REFERENCE = ROOT / "reference.duckdb"
UNIVERSE = ROOT / "market_data" / "yahoo.duckdb"

# ── Per-process resource profile ────────────────────────────────────────────────────────
# The settings and why each one: see the block above the tune(con) call in connect().
# Short version — DuckDB's defaults are per process and sized as if it owned the box
# (a thread per core, 80% of RAM, a 16MB WAL checkpoint, insertion order preserved); four
# shards running at once cannot each have that. Override per run with the DUCKDB_* env
# vars for a one-off heavier job.
_THREADS = int(os.environ.get("DUCKDB_THREADS", "4"))
_MEMORY_LIMIT = os.environ.get("DUCKDB_MEMORY_LIMIT", "3GB")
_CHECKPOINT_THRESHOLD = os.environ.get("DUCKDB_CHECKPOINT_THRESHOLD", "256MB")


def tune(con):
    """Pin the per-process resource profile on an open DuckDB connection.

    Loud on failure for the same reason the TimeZone pin below is: a process that
    silently keeps DuckDB's defaults is one of the four that peg the disk again.
    """
    try:
        con.execute(f"SET threads={_THREADS}")
        con.execute(f"SET memory_limit='{_MEMORY_LIMIT}'")
        con.execute(f"SET checkpoint_threshold='{_CHECKPOINT_THRESHOLD}'")
        con.execute("SET preserve_insertion_order=false")
    except Exception as e:
        print(f"WARNING: could not apply the DuckDB resource profile ({e!r}); this process "
              f"runs at DuckDB defaults (a thread per core, 80% of RAM, 16MB checkpoints) "
              f"— expect heavy disk use while other jobs run")


# What a downloader needs in order to answer "what do I already have?". Snapshot mode copies
# exactly these into staging and then releases market_history, so a long run holds no handle on
# the shared file and cannot block a merge, a repair or a migration.
_SNAPSHOT_TABLES = ("option_bars", "option_bars_checks", "option_minutes",
                    "underlying_minutes", "underlying_daily", "iv_history",
                    "iv_history_checks", "option_flow_daily", "fundamentals_snapshot",
                    "option_greeks_daily", "option_oi_daily")


def connect(log="rw", history=None, universe=False, reference=None, trades_ro=False, staging=None, snapshot=False,
            snapshot_scope=None, snapshot_tables=None, snapshot_columns=None):
    """staging="alpaca" attaches a per-source staging DB **as `mh`**.

    Why: DuckDB allows one writer per file, so every downloader queued behind the same lock
    even though Alpaca, Futu, TWS and akshare have independent rate limits. With staging,
    each source writes its own file concurrently and `merge_staging.py` folds them into
    market_history afterwards. Writers need no other change: their `mh.<table>` references
    now resolve to their own staging file.

    Open the trading DB as the default catalog and ATTACH the others.

    log/history/reference: "rw" | "ro" | None (None = don't open that file at all).
    universe:  True attaches the yahoo warehouse read-only as `yh`.
    Structure is read-mostly, so `reference="ro"` is the normal choice.

    snapshot_scope / snapshot_tables / snapshot_columns narrow the copy a snapshot run
    takes of market_history. A shard that only reads option_bars for its own tickers
    passes all three and copies ~1% of what the default 11-table copy would — see
    _snapshot_ctas() for the measurements, and for why tables without `underlying` are
    never scope-filtered.

    TRAP: DuckDB's read_only applies to the WHOLE instance, so opening the log
    read-only also makes every attachment read-only. A script that must WRITE an
    attachment while only reading the log passes log=None with trades_ro=True —
    the log is then attached read-only as `tr` (write `tr.fills`, not `main.fills`).
    """
    # Consumer-only connections pin a published release. Ingestion and staging keep
    # their existing transactional paths and module objects for legacy callers.
    if not staging and not snapshot and log in (None, "ro") and history in (None, "ro") and reference in (None, "ro"):
        from data_providers.lake import current, configured_connection
        release = current(ROOT) if os.environ.get("DATA_PROVIDERS_BACKEND", "auto") != "duckdb" else None
        if release and (not (log or trades_ro) or "trades" in release.manifest["stores"]):
            con = configured_connection()
            try:
                if log:
                    release.attach(con, "trades")
                elif trades_ro:
                    release.attach(con, "trades", "tr")
                for enabled, store, alias in ((history, "history", "mh"), (reference, "reference", "ref"),
                                              (universe, "universe", "yh")):
                    if enabled:
                        release.attach(con, store, alias)
                return con
            except Exception:
                con.close()
                raise
    if log:
        con = duckdb.connect(str(TRADES), read_only=(log == "ro"))
    else:
        con = duckdb.connect(":memory:")
        if trades_ro:
            con.execute(f"ATTACH '{TRADES}' AS tr (READ_ONLY)")
    # ── UTC, set once, for every connection ──────────────────────────────────────────────
    # DuckDB stamps `now()` and `current_timestamp` in the SESSION timezone, which on this
    # box is Asia/Shanghai -- so every SQL-written timestamp was 8 hours ahead of the UTC
    # that clock.py produces and the rest of the warehouse stores. That is not a cosmetic
    # offset: a freshness check comparing a SQL-stamped row against a Python-stamped one
    # sees an 8-hour-old book as current, or a current one as stale.
    #
    # Setting it here rather than at each call site means a new script cannot get it wrong,
    # and SQL `now()` finally agrees with clock.now().
    try:
        con.execute("SET TimeZone='UTC'")
    except Exception as e:
        # Deliberately NOT silent. If this fails the session keeps the box's Asia/Shanghai
        # zone and every SQL-written timestamp runs 8h ahead of every Python-written one --
        # which is the exact bug this line exists to prevent, now happening invisibly.
        print(f"WARNING: could not pin the DuckDB session to UTC ({e!r}); "
              f"SQL current_timestamp is in the box's local zone, not UTC")

    # ── DuckDB defaults are for ONE process on an idle box. This box runs several at once. ──
    #
    # Measured 2026-10-03 while 4-16 downloader shards ran: the device sat at ~100% utilisation
    # moving only ~230 kB/s (iodelay 1.2 s, iostat). %util at 100% with almost no throughput is
    # many small random operations, not a bandwidth wall — and DuckDB's defaults cause exactly
    # that under concurrency:
    #
    #   * threads           — defaults to one per CORE, so four shards each start 16-thread
    #                         query pools on a 16-core box: 64 threads competing, and every
    #                         extra reader pushes the writer's dirty pages out of cache.
    #   * checkpoint_threshold (16MB default) — the WAL is checkpointed and fsynced constantly
    #                         during a write-heavy sweep, so a few hundred kB/s of real data
    #                         turns into a storm of small writes. 256MB is 16x the default:
    #                         enough to stop the storm, small enough that a checkpoint is still
    #                         a brief pause rather than one long stall on a slow disk.
    #   * memory_limit      — defaults to 80% of RAM PER PROCESS. Four processes each promising
    #                         themselves 12 GB on a 15 GB box is a promise they cannot keep, and
    #                         the shortfall is paid in re-reads from disk.
    #
    # These are conservative on purpose: they bound each process so several can coexist without
    # fighting. A single-process batch job that wants the whole box can override them with
    # `SET` on its own connection.
    #
    # Both are best-effort — an older DuckDB that spells a setting differently must not stop a
    # download — but a failure is printed rather than swallowed, so tuning that silently did
    # nothing is visible instead of being believed.
    # tune() applies exactly the profile described above; shared rather than inlined so the
    # direct-connect scripts (merge_staging, download_guard) cannot drift from it.
    tune(con)
    # Consequence worth knowing: with the session pinned to UTC, `current_timestamp` is a
    # TIMESTAMPTZ and the Python client returns it tz-AWARE. Inserting it into a naive
    # TIMESTAMP column stores the correct UTC wall time (verified against clock.now()), but
    # comparing a bare `SELECT current_timestamp` with a naive datetime raises
    # "can't subtract offset-naive and offset-aware datetimes".
    # Need a naive value in SQL? CAST it: `SELECT CAST(current_timestamp AS TIMESTAMP)`.
    # The tables a snapshot copies — the default list unless the caller narrowed it (a
    # shard that only reads option_bars should not pay for all eleven).
    snapshot_tables = tuple(snapshot_tables) if snapshot_tables else _SNAPSHOT_TABLES

    def _snapshot_ctas(_t):
        """(sql, params) for one snapshot copy — projected and scoped when asked to be.

        The unscoped copy is what pegs the disk: option_bars alone is 15.6M rows, and every
        concurrent shard copied all 11 tables (~22.35M rows, ~1.2GB) even though a shard
        only ever asks about its own ~20 tickers. Measured 2026-10-03: four shards launching
        together put the disk at 100% read for minutes.

        The scope filters on `underlying` where the table has one. Tables without it are
        copied whole: their `symbol` can be an OCC contract symbol, and filtering those by a
        ticker list would copy nothing — turning "what do we already have?" into a silent
        "nothing", which re-fetches the universe. Loud and big beats silent and empty.
        """
        cols = ("*" if not snapshot_columns or _t not in snapshot_columns
                else ", ".join(snapshot_columns[_t]))
        where, params = "", []
        if snapshot_scope:
            have = {r[0] for r in con.execute(
                ("SELECT column_name FROM duckdb_columns() "
                 "WHERE database_name='_live' AND table_name=?"), [_t]).fetchall()}
            if "underlying" in have:
                vals = list(snapshot_scope)
                where = " WHERE underlying IN (" + ",".join(["?"] * len(vals)) + ")"
                params = vals
        return (f"CREATE OR REPLACE TABLE mh._snap_{_t} AS "
                f"SELECT {cols} FROM _live.{_t}{where}", params)

    if staging:
        sp = ROOT / "staging" / f"{staging}.duckdb"
        sp.parent.mkdir(exist_ok=True)
        con.execute(f"ATTACH '{sp}' AS mh")
        # `mhro` = what we already hold, so a staged run does not re-fetch it. Best effort:
        # DuckDB blocks even READ-ONLY access while another process writes the file, which is
        # exactly the situation staging exists for. If it cannot attach, we fall back to views
        # over staging (no re-fetch protection, but nothing crashes and nothing duplicates —
        # ON CONFLICT still guards the merge).
        from data_providers.data.schema import history as _d          # same DDL as the real file, created on demand
        _d.ensure(con)
        attached_live = False
        try:
            if snapshot:
                # ── SNAPSHOT MODE: read what we hold, then LET GO of the file ───────────────
                #
                # The problem this solves: a downloader attaches market_history read-only and
                # holds it for its ENTIRE run — hours. DuckDB readers block writers, so no
                # merge, repair or migration can open the file for writing while any
                # downloader is alive. On 2026-10-01 that forced six separate stop-the-world
                # windows (the CZCE expiry repair, the CN split phase 2, two symbol-map loads,
                # two restarts).
                #
                # The downloaders do not actually need the file — they need the ANSWER to
                # "what do I already have?", once, at startup. So take that answer now, copy
                # it into staging, and DETACH. The run then holds nothing but its own staging
                # file, and market_history is free for writers the whole time.
                #
                # Copied rather than projected, so query semantics are unchanged: callers
                # still write `mhro.iv_history` and still get the same columns.
                con.execute(f"ATTACH '{HISTORY}' AS _live (READ_ONLY)")
                # `except: pass` assumed the ONLY failure is "table absent in the target".
                # It also swallows disk-full, OOM and lock contention -- and the code then
                # set attached_live = True regardless, so an incomplete snapshot was
                # indistinguishable from a complete one. Record what actually failed.
                #
                # `_fell_back` is initialised HERE, not next to the view loop below, because
                # it is READ before that point (the "N views fell back" report). It used to be
                # assigned only at the loop, so reading it raised NameError, the outer handler
                # caught it, and a snapshot that had copied every table and built every view
                # still declared itself FAILED -- telling the operator that the run would
                # re-fetch data we hold, when it would not. Measured 2026-10-07: mhro held
                # 4,588,779 greeks rows under that warning.
                snap_failed, _fell_back = [], []
                for _t in snapshot_tables:
                    try:
                        _sql, _ps = _snapshot_ctas(_t)
                        con.execute(_sql, _ps)
                    except Exception as _e:
                        snap_failed.append((_t, repr(_e)[:90]))
                con.execute("DETACH _live")
                # THIS LINE IS THE POINT OF SNAPSHOT MODE. Without it `attached_live` stays
                # False, the fallback below builds `mhro` as views over the EMPTY staging
                # file, and every skip/coverage query answers "we hold nothing" -- so the run
                # re-fetches the entire universe while the snapshot tables it just built sit
                # unused in mh._snap_*. The snapshot always "succeeded" and was never used.
                #
                # Measured cost: 753,014 rows re-fetched by one job, and every shard launched
                # tonight did the same until this was found. The `else` branch set this flag;
                # the snapshot branch never did, and nothing tested that the snapshot was
                # actually READ rather than merely written.
                attached_live = True
                print("(mhro snapshotted into staging — market_history released for writers)")
                # SAY WHAT DID NOT WORK. A failed copy or a staging fallback means some
                # `mhro.x` answers "empty" while market_history actually holds rows --
                # the caller then re-downloads data we already have. That is the
                # 753,014-row re-fetch this whole block exists to prevent, and it was
                # silent in both cases.
                if snap_failed:
                    print(f"  !! snapshot INCOMPLETE — {len(snap_failed)} of "
                          f"{len(snapshot_tables)} table(s) could not be copied:")
                    for _t, _e in snap_failed:
                        print(f"       {_t}: {_e}")
                if _fell_back:
                    print(f"  !! {len(_fell_back)} view(s) fell back to STAGING, which is "
                          f"EMPTY at the start of a run — a caller reading these will "
                          f"believe we hold nothing: {', '.join(_fell_back[:6])}")
                # EXPOSE the snapshot, or none of the above mattered.
                #
                # Setting attached_live skips the fallback block below, and that block is the
                # ONLY place `mhro.*` gets built. So the tables were copied into mh._snap_* and
                # then nothing read them: every `mhro.x` query raised CatalogException, and
                # callers that fall back on error (download_option_bars._read tries mhro first,
                # then mh.) silently dropped to `mh.x` -- their own STAGING file, which is EMPTY
                # at the start of a run. The skip set therefore contained only what this shard
                # had staged itself, never what market_history already held.
                #
                # Verified 2026-10-02 with a throwaway staging key: `mhro` had 0 tables while
                # mh._snap_option_greeks_daily held 1,462,143 rows. Same class of waste as the
                # 753,014-row re-fetch described below; this is the half that was still live.
                con.execute("CREATE SCHEMA IF NOT EXISTS mhro")
                for _t in snapshot_tables:
                    try:
                        con.execute(f"CREATE OR REPLACE VIEW mhro.{_t} AS "
                                    f"SELECT * FROM mh._snap_{_t}")
                    except Exception:
                        # Table absent from the snapshot (it does not exist in market_history,
                        # or the copy above failed). Fall back to staging, matching the
                        # non-snapshot branch, so a caller gets an empty answer rather than a
                        # CatalogException it might not handle.
                        try:
                            con.execute(f"CREATE OR REPLACE VIEW mhro.{_t} AS "
                                        f"SELECT * FROM mh.{_t}")
                            _fell_back.append(_t)
                        except Exception:
                            pass
            else:
                con.execute(f"ATTACH '{HISTORY}' AS mhro (READ_ONLY)")
                attached_live = True
        except Exception:
            attached_live = False
        if not attached_live:
            # ── SAY SO, LOUDLY ───────────────────────────────────────────────────────────
            # When snapshot mode could not capture market_history, `mhro` ends up as views
            # over STAGING, which is empty at the start of a run. Every "what do I already
            # have?" query then returns 0 rows with no error, and the downloader re-fetches
            # the entire universe while believing it started from nothing.
            #
            # This is not theoretical. On 2026-10-02 a priority run launched while another
            # job held market_history re-downloaded 275,320 option bars that were already
            # stored -- 86.6% of its output -- because this fallback was silent. The old
            # comment called it "no re-fetch protection, but nothing crashes and nothing
            # duplicates", which was true about data integrity and completely wrong about
            # cost: the duplicated FETCHING is the thing the skip logic exists to prevent.
            #
            # The fallback stays (nothing can read a file another process is writing), but
            # it can no longer masquerade as normal operation.
            _why = "snapshot capture FAILED" if snapshot else "market_history not attached"
            print(f"WARNING: {_why} — `mhro` is a view over STAGING (EMPTY at start). "
                  f"Skip/coverage logic will see NOTHING as already held and this run WILL "
                  f"re-fetch data we already have. Stop and retry when no writer holds "
                  f"market_history if the quota matters.")
            con.execute("CREATE SCHEMA IF NOT EXISTS mhro")
            for _t in snapshot_tables:
                _src = f"mh._snap_{_t}" if snapshot else f"mh.{_t}"
                try:
                    con.execute(f"CREATE OR REPLACE VIEW mhro.{_t} AS SELECT * FROM {_src}")
                except Exception:
                    con.execute(f"CREATE OR REPLACE VIEW mhro.{_t} AS SELECT * FROM mh.{_t}")

    elif history:
        con.execute(f"ATTACH '{HISTORY}' AS mh"
                    + ("" if history == "rw" else " (READ_ONLY)"))
    if reference:
        con.execute(f"ATTACH '{REFERENCE}' AS ref"
                    + ("" if reference == "rw" else " (READ_ONLY)"))
    if universe:
        con.execute(f"ATTACH '{UNIVERSE}' AS yh (READ_ONLY)")
    return con
