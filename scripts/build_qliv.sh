#!/bin/bash
# build_qliv.sh — build the vendored QuantLib, then the qliv C-ABI shim that links it.
#
# Two stages, because they change at different rates:
#   1. QuantLib itself (third_party/QuantLib, ~46 MB of C++, ~10 min at -j16). Skipped once
#      libQuantLib.a exists, so re-running this after a shim edit takes seconds.
#   2. qliv.so — the shim plus the trinomial pricer. Always rebuilt; two translation units.
#
# Stage 1 needs cmake and Boost HEADERS only (QuantLib defaults to std::any/optional/
# shared_ptr, so the Boost libraries are never linked). Both come from apt:
#   apt-get install -y cmake libboost-dev
#
#   bash scripts/build_qliv.sh [--force]     # --force re-runs the QuantLib build too
set -euo pipefail
cd "$(dirname "$0")/.." || { echo "cannot cd to the repo root"; exit 1; }
ROOT="$PWD"
QL_SRC="$ROOT/third_party/QuantLib"
QL_BUILD="$ROOT/third_party/QuantLib-build"
QL_LIB="$QL_BUILD/ql/libQuantLib.a"
OUT="$ROOT/third_party/qliv/qliv.so"
OBJ="$ROOT/third_party/qliv/obj"

[ -f "$QL_SRC/CMakeLists.txt" ] || { echo "missing vendored source: $QL_SRC" >&2; exit 1; }

if [ "${1:-}" = "--force" ] || [ ! -f "$QL_LIB" ]; then
    if [ -f "$QL_BUILD/CMakeCache.txt" ] && ! grep -Fqx "CMAKE_HOME_DIRECTORY:INTERNAL=$QL_SRC" "$QL_BUILD/CMakeCache.txt"; then
        # CMake caches absolute source paths. Preserve relocated output and configure afresh.
        mv "$QL_BUILD" "$QL_BUILD.relocated-$(date -u +%Y%m%dT%H%M%S)"
    fi
    echo "=== [1/2] QuantLib $(grep -oE '1\.[0-9]+' "$QL_SRC/ql/version.hpp" | head -1) from source ==="
    cmake -S "$QL_SRC" -B "$QL_BUILD" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_FLAGS_RELEASE="-O3 -DNDEBUG -march=native" \
        -DQL_BUILD_TEST_SUITE=OFF \
        -DQL_BUILD_EXAMPLES=OFF \
        -DQL_BUILD_BENCHMARK=OFF \
        -DQL_ENABLE_OPENMP=OFF \
        -DBUILD_SHARED_LIBS=OFF > /dev/null
    # -j16 is not arbitrary: this box has 16 cores and the build is pure compile, so it is
    # the difference between ~10 minutes and ~2 hours.
    cmake --build "$QL_BUILD" -j"$(nproc)"
else
    echo "=== [1/2] QuantLib static lib already built: $QL_LIB ==="
fi

echo "=== [2/2] qliv shim ==="
mkdir -p "$OBJ"
# -O3 -march=native -std=c++20: this is the hot path, and the box is fixed, so tuning for it
# is free. NOTE: -march=native makes the .so host-specific — it will not run on a different
# CPU. If this ever needs to ship, drop it to -march=x86-64-v3.
COMMON="-O3 -march=native -std=c++20 -fPIC -DNDEBUG -I$QL_SRC"

# qliv.cpp — European Black-Scholes. Compiled WITH OpenMP, because its only pragma sits on
# the outer loop of the batch entry point: one parallel region per batch, which is what you
# want.
g++ $COMMON -fopenmp -c "$ROOT/third_party/qliv/qliv.cpp" -o "$OBJ/qliv.o"

# qliv_tree.cpp — American trinomial. Compiled WITHOUT OpenMP, deliberately.
#
# QuantLib's TreeLattice::stepback carries `#pragma omp parallel for`
# (ql/methods/lattices/lattice.hpp:168), and rollback calls stepback once per TIME STEP. On a
# 200-step tree that opens 200 thread teams for a single price, each guarding a loop of a few
# dozen adds — the fork/join cost dwarfs the arithmetic. Measured on this box: 19.6 ms per
# price with OpenMP on, and one IV solve is ~80 prices, i.e. 1.6 SECONDS per implied vol.
#
# With -fopenmp absent the pragma is inert and the tree runs serially, which is far faster at
# this size. Parallelism for this file belongs at the BATCH level instead, where each thread
# owns whole options — see the std::thread pool in ql_am_iv_batch. Two layers of parallelism
# here would only fight each other.
g++ $COMMON -c "$ROOT/third_party/qliv/qliv_tree.cpp" -o "$OBJ/qliv_tree.o"

# Link. -fopenmp is needed here only to resolve the symbols qliv.o references.
g++ -shared -fopenmp "$OBJ/qliv.o" "$OBJ/qliv_tree.o" "$QL_LIB" -o "$OUT"

echo "built: $OUT"
ls -la "$OUT"
