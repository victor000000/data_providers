#!/bin/bash
# cn_topup.sh — close the CN gaps that are ACTUALLY open, in one writer's order.
#
# Measured 2026-10-11 against cn_market.duckdb, per product (iv_max / bar_max):
#   DCE B,C,EB,EG,I   IV ends 10-08 while their bars end 10-09  → solver, one session
#   DCE PP            IV and bars end 09-30                     → solver, ~6 sessions
#   GFEX LC,SI        IV and bars end 09-30                     → venue builder
#   SHFE AG,RB,BR     bars only from 09-28 (seeded 10-10)       → deep backfill below
#   everything else   current through 10-09 (Friday); the steps below SKIP it
#
# WHY NOT `update_all.sh --cn-only`, which is the sanctioned orchestrator: it is right for
# a full pass, and this is not one. Its CZCE step re-buys the previous calendar year for
# every product (~26 requests) to gain nothing on a Sunday, and its SHFE step takes the
# products that are CURRENT and leaves the three that are SHALLOW — the skip is recency,
# not depth. The steps here are the same programs, called with the gaps they exist for.
#
# SERIAL ON PURPOSE: cn_market.duckdb allows one writer, so these four run in order. The
# last one is the long pole (~1,200 day-calls); everything before it is minutes.
#
#   scripts/cn_topup.sh          # idempotent: steps that find nothing to do are no-ops
set -u
cd "$(dirname "$0")/.." || exit 1
CODE_HOME="$PWD"
PYTHON="$CODE_HOME/.venv/bin/python"
export DATA_PROVIDERS_HOME="${DATA_PROVIDERS_HOME:-$("$PYTHON" -c 'from data_providers.runtime import ROOT; print(ROOT)')}"
cd "$DATA_PROVIDERS_HOME" || exit 1

stamp() { date -u +%FT%TZ; }
PY="$PYTHON"

echo "--- CN top-up $(stamp) ---"
# 1. CZCE carries IV in its own yearly file — one request per product-year, so this is the
#    cheap way to be sure nothing CZCE slipped. Current products answer with a skip.
$PY -m data_providers.jobs.history.build_iv_history_cn_bulk --years 2025 2026
# 2. SHFE/INE/GFEX publish one trading day per request; GFEX is the venue that is behind.
$PY -m data_providers.jobs.history.build_iv_history_cn_venue --venues SHFE,INE,GFEX
# 3. DCE answers HTTP 412, so its IV is solved from the Sina chain — the same requests also
#    bring the option bars (--bars), which is why this is one call and not two.
$PY -m data_providers.jobs.history.build_iv_history_cn_solver --venues DCE --bars
# 4. THE DEEP BACKFILL. AG/RB/BR were seeded 2026-10-10 with a short window and their bar
#    history is 9 sessions long. --force is required because they are RECENT (so the
#    recency skip would pass them) but not DEEP, which is the hole; --products confines
#    that force to the three that need it, so the other SHFE products are untouched.
#    --days 400 rather than 640: ~18 months back, and the venue IV for these products
#    already reaches 2022 in iv_history, so bars are the only thin series.
$PY -m data_providers.jobs.data_ops.fetch_cn_products --venues SHFE --products AG RB BR --days 400 --force
echo "--- CN top-up done $(stamp) ---"
