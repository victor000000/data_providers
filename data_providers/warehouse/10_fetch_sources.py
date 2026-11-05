#!/usr/bin/env python3
"""Snapshot every external membership source into sources/wikipedia/.

Saves raw HTML (live pages) and raw wikitext (pinned revisions) so the
membership build is reproducible even after Wikipedia edits the articles.
Writes sources/wikipedia/PROVENANCE.md recording exactly what was fetched.

Workspace download rule: never re-download what is already on disk. This is the
one script that deliberately does, and it should be run with that in mind - a
snapshot is a copy of a LIVE page, so re-fetching is how it is kept current, and
the run overwrites the frozen copies the membership build and 80_crosscheck.py
read. It is six requests, which is why it is left as a full refresh rather than
gated behind a flag; pass page keys (`10_fetch_sources.py sp500_live`) to
refresh only those and leave the rest of sources/wikipedia/ untouched.
"""
import datetime
import json
import os
import sys

import requests

from data_providers.warehouse import config as C

from data_providers.warehouse import helpguard  # noqa: E402  (a no-argument script must not
helpguard.handle_help(__file__)  #  do its work because someone asked --help)

API = "https://en.wikipedia.org/w/api.php"


def get(session, url, **params):
    r = session.get(url, params=params or None, timeout=60)
    r.raise_for_status()
    return r


def main():
    os.makedirs(C.WIKI_RAW, exist_ok=True)
    s = requests.Session()
    s.headers.update({"User-Agent": C.UA})
    prov = []

    # Optional key filter: `10_fetch_sources.py sp100_live` refetches only that
    # page. Without it every page is refreshed, which is the intended full run
    # but is wasteful (and churns the snapshot dates) when one page is added.
    only = set(sys.argv[1:])
    unknown = only - set(C.WIKI_PAGES)
    if unknown:
        print(f"unknown page key(s): {sorted(unknown)}")
        print(f"known keys: {sorted(C.WIKI_PAGES)}")
        sys.exit(2)
    if only:
        # keep the provenance of the pages we are not touching, so the file
        # still describes the whole sources/ directory
        keep = {}
        p = os.path.join(C.WIKI_RAW, "PROVENANCE.md")
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                if line.startswith("| `"):
                    f = line.split("`")[1].rsplit(".", 1)[0]
                    keep[f] = line.rstrip("\n")
        for k in sorted(keep):
            if k not in only:
                prov.append(keep[k])

    for key, spec in C.WIKI_PAGES.items():
        if only and key not in only:
            continue
        try:
            if "oldid" in spec:
                # rendered HTML, not wikitext: every parser then reads the same
                # shape whether the source is a live page or a pinned revision
                r = get(s, API, action="parse", oldid=spec["oldid"],
                        prop="text", format="json", formatversion=2)
                text = r.json()["parse"]["text"]
                ext, ref = ".html", f"oldid={spec['oldid']}"
            else:
                r = get(s, spec["url"])
                text, ext, ref = r.text, ".html", spec["url"]
            path = os.path.join(C.WIKI_RAW, key + ext)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"  [ok]   {key:20} {len(text):>9} bytes  {ref}")
            prov.append(f"| `{key}{ext}` | {spec['title']} | {ref} | {spec['why']} |")
        except Exception as e:                                   # noqa: BLE001
            print(f"  [FAIL] {key:20} {type(e).__name__}: {str(e)[:90]}")
            prov.append(f"| `{key}` | {spec.get('title')} | FAILED: {str(e)[:60]} | - |")

    with open(os.path.join(C.WIKI_RAW, "PROVENANCE.md"), "w") as fh:
        fh.write("# Membership source provenance\n\n")
        fh.write(f"Fetched {datetime.date.today().isoformat()}.\n\n")
        fh.write("| file | wikipedia article | reference | why this source |\n")
        fh.write("|---|---|---|---|\n")
        fh.write("\n".join(prov) + "\n")
    print(f"\nprovenance -> {C.WIKI_RAW}/PROVENANCE.md")


if __name__ == "__main__":
    main()
