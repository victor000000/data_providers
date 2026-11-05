#!/bin/bash
# backfill_cn_history.sh — extend CN option IV history BACKWARDS, as deep as each source serves.
#
# WHY THIS IS SEPARATE FROM update_all.sh. update_all.sh maintains the series FORWARD: its
# builders default to --start 2025-01-02 and are gap-aware, so a routine run fills recent holes
# and never touches the past. Deep history is a different job with a different cost shape, and
# it is bounded per product by when that product's OPTIONS LISTED — not by anything in the
# database, which has no listing-date column anywhere (cn_product_map carries none, and
# akshare's own CZCE year map covers CZCE only).
#
# THE LISTING DATES BELOW ARE MEASURED, NOT ASSUMED (probed 2026-10-08 against each endpoint).
# They are what keeps this from spending thousands of requests asking a venue for days before
# its options existed. Source floors, all verified live:
#
#   CZCE   option_hist_yearly_czce serves 2020+ (2019 and earlier return an error page that
#          parses to ~16 unnamed rows); per product: CF/MA/RM/SR/TA/ZC 2020, OI/PK 2022,
#          PX/SA/SH/UR 2023, FG 2024 (and nothing before it).
#   SHFE   option_vol_shfe serves any past day; depth is the listing date below.
#   INE    same endpoint as SHFE.
#   GFEX   option_hist_gfex; SI listed 2022-12-23, LC 2023-07-21.
#   CFFEX  Sina zz1000 ladders/contracts from 2022-08 (MO listed 2022-07-22).
#   DCE    Sina contract history from ~2019 (m1709/1801/1809 and m1901 have no page; m1909+ do).
#
# EVERY LEG IS BOUNDED WITH --end AT THE STORED FLOOR, and that is not optional for the solver:
# its `have` filter drops stored days from the OUTPUT only, so without --bars it still issues
# every request for months already held. The venue builder does skip stored days, but an --end
# there keeps a re-run honest about its scope.
#
# SERIAL BY NECESSITY: one writer per DuckDB file, and akshare's throttle is a GLOBAL flock
# (shared/net.py), so concurrent legs would interleave 1-for-1 and gain nothing. The lever on
# wall-clock is the cap itself: AKSHARE_LIMIT=120/30 is ~3.8 req/s, up from the 2 req/s default.
#
# BUT NEITHER THE GATE NOR THE WORKER COUNT SETS THE PACE -- measure before tuning either.
# Measured 2026-10-08 on the SHFE walk: copper did 1,521 requests in 14 min at --workers 4
# (1.8 req/s); rubber did 855 in 436 s at --workers 8 (2.0 req/s). Doubling the workers bought
# 11%, not the 2x the arithmetic suggests, and the raised gate was never the constraint. The
# reading that fits both numbers is that SHFE caps a client near 2 req/s however many sockets
# it opens -- the same server-side serialisation cn_iv/venue.py:8 records for GFEX, which
# refuses outright above it. So this walk costs ~2 req/s per venue whatever the settings, and
# the only real lever is starting it sooner. AKSHARE_LIMIT still matters as a second ceiling
# to get out of the way, but it is not what sets the pace, and W=8 is kept only because it is
# marginally ahead -- do not read it as a tuned value.
#
# RESUMABLE: every leg skips what is stored, so stopping and re-running costs only the gap.
#   ~29k requests; the SHFE/INE walk is ~8.4k of it and is the slowest per request.
cd "$(dirname "$0")/.." || { echo "cannot cd to the repo root"; exit 1; }
CODE_HOME="$PWD"
PYTHON="$CODE_HOME/.venv/bin/python"
export DATA_PROVIDERS_HOME="${DATA_PROVIDERS_HOME:-$("$PYTHON" -c 'from data_providers.runtime import ROOT; print(ROOT)')}"
cd "$DATA_PROVIDERS_HOME" || exit 1

export AKSHARE_LIMIT=120/30
PY="$PYTHON"
W=8
leg() {  # leg <label> <tail> <cmd...>
  local label="$1" n="$2"; shift 2
  echo "=== $label  $(date -u +%FT%TZ) ==="
  "$@" 2>&1 | tail -"$n"
  echo "--- $label rc=${PIPESTATUS[0]}  $(date -u +%FT%TZ) ---"
}

# 1. CZCE — one request per product-YEAR. CF/MA/RM/SR/TA/ZC gain four years, OI/PK two,
#    PX/SA/SH/UR one, FG none (it listed in 2024; the endpoint has no earlier year for it).
leg "CZCE 2020-2023" 12 $PY -m data_providers.jobs.history.build_iv_history_cn_bulk \
    --products CF FG MA OI PK PX RM SA SH SR TA UR ZC --years 2020 2021 2022 2023
# MA's stored series began 2025-04-03, leaving 2024 and Q1-2025 as a HOLE in the middle of an
# otherwise 2020-2026 series. A hole is worse than a short series: every window that spans it
# ranks the latest IV against a sample missing fifteen months and reports it as a percentile.
leg "CZCE MA gap 2024-2025" 4 $PY -m data_providers.jobs.history.build_iv_history_cn_bulk \
    --products MA --years 2024 2025

# 2. GFEX / CFFEX — both listed late, so these are short and cheap.
#
# GFEX REFUSES THE RAISED RATE, measured 2026-10-08: at AKSHARE_LIMIT=120/30 with --workers 4
# both products came back "REFUSED — 34 request(s) failed and the last 40 were almost all
# failures", and nothing was written at all. That is the same refusal cn_iv/venue.py:8 records
# for 8 workers. This leg therefore overrides itself back to the default 2 req/s and 2 workers;
# raising it for the other venues is fine, raising it here loses the leg outright.
leg "GFEX SI+LC" 6 env AKSHARE_LIMIT=60/30 $PY -m data_providers.jobs.history.build_iv_history_cn_venue \
    --venues GFEX --start 2022-12-23 --end 2024-10-28 --workers 2
leg "CFFEX MO" 6 $PY -m data_providers.jobs.history.build_iv_history_cn_solver \
    --venues CFFEX --start 2022-08-01 --end 2024-11-03 --workers $W

# 3. SHFE + INE — one request per product-DAY, deepest product first. Each product starts at
#    its OWN listing date: one conservative --start per venue would spend ~35 min asking for
#    days before RB/AG/BR existed, and every such day is a throttled request returning nothing.
leg "SHFE CU"    4 $PY -m data_providers.jobs.history.build_iv_history_cn_venue --venues SHFE --products CU --start 2018-09-21 --end 2024-12-31 --workers $W
leg "SHFE RU"    4 $PY -m data_providers.jobs.history.build_iv_history_cn_venue --venues SHFE --products RU --start 2019-01-28 --end 2024-12-31 --workers $W
leg "SHFE AU"    4 $PY -m data_providers.jobs.history.build_iv_history_cn_venue --venues SHFE --products AU --start 2019-12-20 --end 2024-12-31 --workers $W
leg "SHFE AL+ZN" 4 $PY -m data_providers.jobs.history.build_iv_history_cn_venue --venues SHFE --products AL ZN --start 2020-08-10 --end 2024-12-31 --workers $W
leg "INE SC"     4 $PY -m data_providers.jobs.history.build_iv_history_cn_venue --venues INE --products SC --start 2021-06-21 --end 2024-12-31 --workers $W
leg "SHFE RB+AG" 4 $PY -m data_providers.jobs.history.build_iv_history_cn_venue --venues SHFE --products RB AG --start 2022-12-26 --end 2024-12-31 --workers $W
leg "SHFE BR"    4 $PY -m data_providers.jobs.history.build_iv_history_cn_venue --venues SHFE --products BR --start 2023-07-28 --end 2024-12-31 --workers $W

# 4. DCE — the long pole (~19k requests, ~80 min) and the one leg 2026-10-08's window could not
#    fit. Its own IV is unreadable (HTTP 412), so it is solved from Sina chains. No --bars:
#    bars would roughly double it for data the IVR screener does not rank on.
#
# --synthetic PP=100 IS NOT OPTIONAL. Sina serves 聚丙烯 contracts by code but omits the
# product from the option page `chain_table` scrapes, so without a hand-built grid every
# month of PP answers "not listed" and the run reports success having fetched nothing --
# which is what happened on 2026-10-08 (PP went from 2019 to 2024 in name only). The grid is
# the exchange's own, verified 2026-10-07: on pp2611, 8500/8600/8700/8800 exist and
# 8550/8650 do not -- 100 CNY apart. It is passed for PP ALONE: a step given to a product
# that IS on the page replaces the venue's real ladder with an invented one.
leg "DCE 2019-2024" 8 $PY -m data_providers.jobs.history.build_iv_history_cn_solver \
    --venues DCE --start 2019-01-01 --end 2024-10-31 --workers $W --synthetic PP=100

echo "=== CN history backfill done $(date -u +%FT%TZ) ==="
