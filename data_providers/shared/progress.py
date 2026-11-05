"""progress.py — where a running download says where it is.

Split out of net.py (100-line cap). `cat var/status/<name>.json` answers "where is it now"
without reading a log, and every downloader is resumable because it skips what is already
stored -- so this file is the reason a killed run can be picked up rather than restarted.
"""
import json
import os
import pathlib
import sys
import threading
import time

from data_providers import runtime as bootstrap  # noqa: F401

STATUS_DIR = bootstrap.ROOT / "var" / "status"


def status(name, **fields):
    """Merge fields into var/status/<name>.json (human-readable progress).

    NEVER FATAL: it runs between units of work, so a progress note that raised would end the run
    it describes -- the corrupt-file branch below assumed that; an unwritable DIRECTORY did not."""
    try:
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"note: cannot write status for {name} ({e}) — the run continues", flush=True)
        return
    path = STATUS_DIR / f"{name}.json"
    state = {}
    if path.exists():
        try:
            state = json.loads(path.read_text())
        except Exception:                 # a corrupt status file is not worth failing over
            state = {}
    state.update(fields)
    state["updated_utc"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    # Write-then-rename: write_text() truncates first, so a monitor `cat`ing at the wrong
    # moment reads half-written JSON — a corrupt-file alarm caused by nothing but timing.
    # os.replace() is atomic and the temp stays in this directory, so it cannot cross a
    # filesystem. pid AND thread id, or two workers share a temp path and interleave.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(state, indent=1, default=str))
        os.replace(tmp, path)
    except OSError as e:
        print(f"note: cannot write status for {name} ({e}) — the run continues", flush=True)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
