#!/bin/bash
# run_universe_shards.sh — download the option bars for a whole universe, sharded.
#
# Why shards: the sweep is compute-bound, not quota-bound (measured 4.5% of the Alpaca
# request budget on 2026-10-02), and this box has 16 cores. One process uses one core.
#
# Why a distinct --staging key per shard: DuckDB allows ONE writer per file. Two shards
# sharing `alpaca_bars` means the second blocks or dies. With distinct keys each writes its
# own file and `merge_staging.py` folds them all into market_history at the end.
#
# Resume: each downloader skips any (underlying, expiry) already recorded in its own
# staging file -- staging attaches AS `mh`, so the skip list sees prior runs. Re-running
# this script is therefore the resume mechanism; it never re-fetches what it already has.
#
#   bash scripts/run_universe_shards.sh [tickers-file] [n-shards] [workers-per-shard]
#
# Workers x shards is the box-wide request concurrency. The Alpaca rate gate caps REQUESTS PER
# MINUTE, not concurrency, and ~6 in-flight requests already reach 190/min at ~2 s each — so
# large worker counts add DuckDB contention without adding throughput. 4 workers per shard is
# the default: measured 2026-10-03, four shards at 16 workers each sat at 100% disk with a few
# hundred kB/s of real throughput (16-thread query pools + 16MB WAL checkpoints, x4).
set -u
cd "$(dirname "$0")/.." || { echo "cannot cd to the repo root"; exit 1; }
CODE_HOME="$PWD"
PYTHON="$CODE_HOME/.venv/bin/python"
export DATA_PROVIDERS_HOME="${DATA_PROVIDERS_HOME:-$("$PYTHON" -c 'from data_providers.runtime import ROOT; print(ROOT)')}"
cd "$DATA_PROVIDERS_HOME" || exit 1


TICKERS_FILE="${1:-plans/remaining_download.txt}"
N="${2:-5}"
WORKERS="${3:-4}"
# Downloader flags. Default --dense --gapfill, where --gapfill is LIVE-ONLY (downloader
# change, 2026-10-04): expiries we hold no rows for are fetched; live expiries whose stored
# band is <90% covered are re-asked for the missing strikes — that is how a young expiry's
# band fills in as strikes trade — and dead expiries are skipped.
#
# Why dead expiries are excluded: the old --gapfill re-checked them too, and on the 190-name
# universe every already-covered name re-requested its ~36 stored expiries for the dense
# band's missing strikes and got ZERO rows (14 tickers, ~500 requests, +0 bars; AAPL
# 2024-03-15 probe: 24/24 stored symbols answered, 2/40 missing inner, 0/40 outer — those
# strikes are unlisted or never trade). Pass "--dense" for a pure skip-list run, or
# "--dense --gapfill --gapfill-all" to reopen history.
FLAGS="${4:---dense --gapfill}"
D=$(date -u +%Y%m%d)
OUT="logs/shard_${D}"

[ -f "$TICKERS_FILE" ] || { echo "no such tickers file: $TICKERS_FILE" >&2; exit 1; }

# Split round-robin (stride N), matching how the 2026-10-02 run was sharded: the file stays
# in its own order, so consecutive names land on different shards and a slow name cannot
# stall a whole contiguous block.
rm -f "${OUT}".*.list
awk -v n="$N" -v out="$OUT" '
    /^[[:space:]]*#/ { next }
    NF == 0          { next }
    { print $1 >> (out "." (i % n) ".list"); i++ }
' "$TICKERS_FILE"

echo "=== sharded download start $(date -u '+%F %T') UTC · $(grep -cvE '^\s*(#|$)' "$TICKERS_FILE") tickers · $N shards ==="
pids=()
for i in $(seq 0 $((N-1))); do
    f="${OUT}.${i}.list"
    [ -s "$f" ] || { echo "shard $i: EMPTY, skipped"; continue; }
    echo "--- shard $i: $(wc -l < "$f") tickers -> staging alpaca_bars$i ---"
    # --dense (+-30% at 1%, 61 anchors), not --bulk (+-20% at 2%, 21 anchors).
    #
    # Measured 2026-10-03 against the 2026-11-20 expiry: with the +-20% band, 111 of 418
    # names could not have a 0.16-0.22d leg built at all -- the strike the strategy needs was
    # never fetched. ADBE (spot 237.69) needs a 0.19d put at 208.6 and the band stopped at
    # 230; AFRM needs 59.3 and it stopped at 62.5. The band is anchored at expiry-45d, so a
    # name that rallies into the window loses its downside coverage. The downloader's own
    # note already says +-30% is required at 55-60% IV.
    #
    # It is NOT free: 61 anchors x 2 = 122 symbols per expiry against SYMBOLS_PER_REQUEST=100,
    # so a dense expiry is 2 requests where bulk was 1. (An earlier version of this comment
    # claimed 1, having confused this with download_atm_iv.py's BATCH=300. It was wrong.)
    #
    # --gapfill vs --force is a per-run choice, not a default — see FLAGS above for the
    # 2026-10-04 measurement. --force re-requests EVERY expiry whether or not it is short of
    # strikes, which is the duplication download_guard.py exists to catch. --gapfill asks the
    # narrower question: does the band we are requesting NOW add strikes we do not already
    # hold? It does not re-fetch stored rows (only the missing symbols go in the request),
    # but on already-covered expiries the answer is almost always "no data exists".
    # Stagger the launches: four startup storms (snapshot + first checkpoints) landing in the
    # same second was part of the 2026-10-03 disk peg. Snapshots are scoped now, but a few
    # seconds of offset still keeps the WAL/checkpoint cycles from aligning.
    [ "$i" -gt 0 ] && sleep 5
    "$PYTHON" -m data_providers.jobs.history.download_option_bars \
        --tickers-file "$f" $FLAGS --staging "alpaca_bars$i" \
        --workers "$WORKERS" \
        > "logs/shard${i}_${D}.log" 2>&1 &
    pids+=($!)
done

rc=0
for p in "${pids[@]}"; do wait "$p" || rc=1; done
echo "=== sharded download end $(date -u '+%F %T') UTC rc=$rc ==="
for i in $(seq 0 $((N-1))); do
    echo "--- shard $i tail ---"; tail -3 "logs/shard${i}_${D}.log" 2>/dev/null
done
exit $rc
