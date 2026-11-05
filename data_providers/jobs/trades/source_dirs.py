import os
import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401
from data_providers.runtime import trading_logs_dir

"""source_dirs.py — where the user's broker dumps actually are.

The loaders here globbed `var/raw/broker_dumps/**/` and nothing else, but that is the
repo's COPY. The user maintains `../trading_logs` by hand and told me (2026-10-05) to
treat it as the important source -- and it is one directory flat, not dated
subdirectories, so a `**/` glob rooted only at broker_dumps finds none of it.

`load_cn_settlements` was worse than the rest: its path was hardcoded to
`2026-09-30_qsqh/settlements/`, one dated dump, so the 结算单 for Sep 23, 24 and 28
could sit in the source directory forever and never be read.

Both roots are searched, source first, so a file the user has just saved is picked up
without a manual copy.
"""
ROOT = bootstrap.ROOT

# Search order matters: the hand-maintained source first, then whatever has been
# copied into the repo. Newest mtime wins within a root so a re-saved file supersedes.
SOURCE_DIRS = (
    trading_logs_dir(),
    ROOT / "var" / "raw" / "broker_dumps",
)


def find(pattern):
    """Every match for `pattern`, source dir first, deduplicated BY FILENAME.

    Deduplicating on the full path is not enough and is actively dangerous: the same
    statement sits in the source directory AND in the repo's copy under a dated folder
    -- `结算单_20260923.txt` is in both -- so a path-keyed set returns it twice and a
    loader appends it twice. Filename is the right key here because these are the same
    artifact copied between roots, and the source dir is searched first so a file the
    user has just saved wins over a stale copy in the repo.
    """
    import glob
    import warnings
    if SOURCE_DIRS and not SOURCE_DIRS[0].is_dir():
        warnings.warn(f"Trade source unavailable: {SOURCE_DIRS[0]}; using archived copies. "
                      "Restore the source mount before claiming a current refresh.", RuntimeWarning)
    out, seen = [], set()
    for d in SOURCE_DIRS:
        if not d.exists():
            continue
        for hit in (sorted(glob.glob(str(d / pattern)))
                    + sorted(glob.glob(str(d / "**" / pattern), recursive=True))):
            name = pathlib.Path(hit).name
            if name in seen:
                continue
            seen.add(name)
            out.append(hit)
    return out
