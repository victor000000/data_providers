import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""symbols.py — the resolver every downloader consults before it builds a symbol.

A symbol map that is only a table is documentation, and documentation does not stop bugs.
This is the enforcement half: downloaders ask `symbols.for_source(...)` instead of
concatenating strings, so a wrong spelling fails loudly at the call site rather than
returning empty data that looks like "no options listed".

Three failure modes it exists to stop, all of which happened on 2026-10-01:

1. WRONG SPELLING -> empty result that reads as "no data".
   BRK-B to Alpaca/OCC is HTTP 400 invalid symbol; the whole batch was lost, and the run
   reported it as an absence. OCC roots strip the hyphen (BRKB); IBKR uses a space (BRK B).

2. COLLISION -> a real instrument fetched under a string that means something else.
   'MA' is Mastercard on NYSE and 甲醇 (methanol) on CZCE. Both are real, both resolve, and
   only one is what you meant. Same for 'C' (Citigroup / DCE corn), 'MO' (Altria / CFFEX
   中证1000), 'P' (Pandora / DCE palm oil). A collision is never resolvable from the symbol
   alone — it needs the venue, which is why the canonical key carries it (CN:DCE:C).

3. DUPLICATE DOWNLOAD under two spellings of one instrument.

The map's canonical key encodes the namespace, so an ambiguity is impossible to express:
US names are bare ('MA'), CN products are prefixed ('CN:CZCE:MA'). The source guard
rejects namespaced CN products on equity feeds while retaining legitimate US collisions.
"""
import csv
import pathlib

ROOT = bootstrap.ROOT
CSV = ROOT / "plans" / "symbol_map.csv"

# CN products carry venue-qualified keys; bare symbols continue to identify US equities.
CN_VENUES = ("CZCE", "SHFE", "DCE", "CFFEX", "INE", "GFEX")


class SymbolError(Exception):
    """Raised instead of silently returning the wrong instrument or an empty result."""


def _load():
    m = {}
    if not CSV.exists():
        return m
    with CSV.open() as f:
        for r in csv.DictReader(f):
            m.setdefault(r["canonical"], {})[r["source"]] = r["symbol"]
    return m


_MAP = _load()


def for_source(canonical, source):
    """The spelling `source` wants for `canonical`. Raises if unknown — never guess.

    A guess here is indistinguishable from success at the call site: the vendor simply
    returns nothing, and "nothing" is recorded as "no data".
    """
    if canonical not in _MAP:
        raise SymbolError(f"{canonical!r} is not in the symbol map — add it to "
                          f"plans/symbol_map.csv rather than constructing it by hand")
    if source not in _MAP[canonical]:
        raise SymbolError(f"{canonical!r} has no {source!r} spelling "
                          f"(known: {sorted(_MAP[canonical])})")
    return _MAP[canonical][source]


_REVERSE = None


def canonical_for(source, symbol):
    """Reverse lookup: which canonical instrument a source spells as `symbol`.

    Needed because the map is keyed by CANONICAL (the IBKR spelling) while the downloaders
    hold YAHOO spellings -- they carry BRK-B where the key is 'BRK B'. Without this they
    cannot reach the map at all, which is exactly how they ended up reimplementing the
    translation inline: two spellings of one rule, free to drift, and a drift here is silent
    (a wrong OCC root returns HTTP 400, which the batch loop records as zero bars --
    indistinguishable from a contract that never traded).

    Returns None when unmapped rather than raising: callers use this to *prefer* the map and
    fall back to their own rule, so an unmapped name must not be fatal. Use for_source() when
    you want the strict "never guess" behaviour.
    """
    global _REVERSE
    if _REVERSE is None:
        _REVERSE = {}
        for canon, by_src in _MAP.items():
            for src, sym in by_src.items():
                # First writer wins on a genuine collision; collisions() reports those.
                _REVERSE.setdefault((src, sym), canon)
    return _REVERSE.get((source, symbol))


def translate(symbol, source, target):
    """Resolve a native spelling through the IBKR canonical key without guessing.

    Stock tickers and option roots use separate source names. Return both the
    canonical identity and target spelling so adapters can preserve provenance.
    """
    canonical = canonical_for(source, symbol)
    if canonical is None:
        raise SymbolError(f"No canonical mapping for {source!r} symbol {symbol!r}")
    return canonical, for_source(canonical, target)


def occ_root(canonical):
    """The OCC root: hyphens and dots stripped, uppercased. BRK-B -> BRKB.

    Falls back to the canonical name when a source has no spelling for it. The
    previous form was inverted: `for_source(...) if canonical in _MAP else canonical`
    called for_source only for names it could NOT be sure about, and for_source
    RAISES for a mapped name with no Alpaca entry -- which is every futures contract
    (CL:NYMEX, NQ:IDX) and every CN key. So the mapped-but-unspelled case, the one
    that actually needs the fallback, was the case that raised.
    """
    try:
        s = for_source(canonical, "alpaca")
    except Exception:
        s = canonical
    return "".join(s.split()).replace("-", "").replace(".", "").upper()


def is_cn(canonical):
    return canonical.startswith("CN:")


def collisions():
    """Strings that name two different instruments — the trap that fetched Mastercard for
    methanol. Not an error in itself: 'C', 'MO' and 'P' ARE legitimate US tickers, and the
    CN: namespace already separates them. The danger is only when a CN code travels bare."""
    out = {}
    for canon in _MAP:
        if is_cn(canon):
            bare = canon.split(":")[-1]
            if bare in _MAP:
                out.setdefault(bare, []).append(canon)
    return out


def assert_no_bare_cn(candidates, source):
    """Fail if a CN product code is about to be sent to a NON-CN feed.

    This is the real invariant. The namespace makes the ambiguity unrepresentable, so the
    only way to fall into it is to strip the prefix — which a US fetcher would do by
    accident when it iterates a mixed list. Call this with the symbols you are about to
    request and the source you are about to ask.
    """
    if source == "akshare":
        return True                    # akshare IS the CN feed; bare codes are its language

    # The real invariant is NOT "this string is ambiguous" — 'C', 'MO' and 'P' are perfectly
    # valid US tickers, and refusing them would break legitimate fetches. It is "a NAMESPACED
    # CN key is being sent to a non-CN feed", which only happens when a mixed list is passed
    # through unfiltered. That is the actual mistake, and it is exactly detectable.
    bad = [c for c in candidates if isinstance(c, str) and c.startswith("CN:")]
    if bad:
        raise SymbolError(
            f"refusing to send namespaced CN keys {bad} to {source!r} — a US feed cannot "
            f"resolve them. Filter CN: entries out before a US request (reference."
            f"futures_products is the registry to filter through).")
    return True


def report():
    """Counts by kind, plus every collision — for the checklist run."""
    from collections import defaultdict
    kinds, bysym = defaultdict(int), defaultdict(set)
    with CSV.open() as f:
        for r in csv.DictReader(f):
            kinds[r["kind"]] += 1
            bysym[r["symbol"]].add(r["canonical"])
    return dict(kinds), {k: sorted(v) for k, v in bysym.items() if len(v) > 1}


if __name__ == "__main__":
    k, coll = report()
    print(f"symbol map: {sum(k.values())} rows {k}")
    print(f"ambiguous strings: {len(coll)} — resolved by the CN: namespace, never an error")
    for sym, cs in coll.items():
        print(f"  {sym!r}: {' vs '.join(cs)}")
    # the real guard: a CN code must never reach a US feed
    # Legitimate US tickers that merely LOOK like CN codes must pass:
    try:
        assert_no_bare_cn(["C", "MO", "P", "SPY"], "alpaca")
        print("  guard(alpaca, US-only list): PASS  (C/MO/P are valid US tickers)")
    except SymbolError as e:
        print("  guard(alpaca, US-only list): FAIL —", e)
    # The actual mistake — a namespaced CN key leaking into a US request — must FAIL:
    try:
        assert_no_bare_cn(["SPY", "CN:DCE:C"], "alpaca")
        print("  guard(alpaca, leaked CN key): PASS  <-- WRONG, this should have raised")
    except SymbolError:
        print("  guard(alpaca, leaked CN key): correctly REFUSED")
