#!/usr/bin/env python3
"""Proof-of-concept: pull the constituent names out of an S&P 400 index fund
schedule of investments filed on EDGAR."""


def main():
    import re
    import sys

    # Same guard as every other script in this folder. This one takes a positional
    # path, so `--help` used to try to OPEN a file called "--help" and die on a
    # FileNotFoundError -- read-only, but a flag that asks what a script does should
    # answer, not crash.
    from data_providers.warehouse import helpguard
    helpguard.handle_help(__file__)
    path = sys.argv[1] if len(sys.argv) > 1 else "filing1993.txt"
    d = open(path, errors="replace").read()

    i = d.find("STATEMENT OF INVESTMENTS")
    if i < 0:
        print("no schedule of investments found")
        sys.exit(1)
    head = d[i:i + 200].split("\n")[0]
    j = d.find("STATEMENT OF INVESTMENTS", i + 10)
    section = d[i:j if j > 0 else i + 60000]

    # e.g. " 3,961     Albany International, Cl. A...........    $    71,298"
    pat = re.compile(r"^\s*([\d,]{3,})\s+(?:\(a\)\s*)?(.+?)\.{3,}\s*(?:\$)?\s*([\d,]+)\s*$")
    names = []
    for line in section.split("\n"):
        m = pat.match(line)
        if not m:
            continue
        nm = m.group(2).strip()
        if nm and not nm.isupper() and len(nm) > 2:
            names.append(nm)

    print(f"schedule: {head.strip()}")
    print(f"holdings parsed: {len(names)}")
    print("first 10:", names[:10])
    import os
    out = os.path.join(str(__import__("data_providers.runtime", fromlist=["ROOT"]).ROOT / "market_data"),
                       "sources", "independent", "sp400_1993_holdings_sample.txt")
    with open(out, "w") as fh:
        fh.write("\n".join(names) + "\n")
    print("saved ->", out)


# Guard added 2026-10-05. This script executed top-to-bottom at module level, so
# merely IMPORTING it ran the whole job -- and since it reads fixed paths from a
# migration that finished (/workspace/..., filing1993.txt) that do not exist here,
# any import raised FileNotFoundError. That made market_data/ unimportable, which
# in turn forced check_all.sh to exclude the whole subproject from its import
# sweep -- weakening the gate to accommodate four archived scripts.
if __name__ == "__main__":
    main()
