import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""download_fill_contracts.py — COMPATIBILITY SHIM. The real module is apps.data_ops.download_fill_contracts.

Kept so both invocation styles keep working during the data/application split:

    uv run python apps/trade_log/download_fill_contracts.py ...   (forwards to __main__)
    import download_fill_contracts                                                    (aliases the real module)

Is anything still using this? `python scripts/shim_status.py` answers it.
Do NOT hand-grep for the name: the shim's own docstring contains it, so the
grep always finds this file -- and the real module's usage example names the
old path too. Both were counted as "referenced" for months.
That tool sees IN-REPO references only. A shim is also an entry point a human
or an external script may call by path, which no grep can see, so treat its
"no references" as necessary, not sufficient.
"""
import pathlib
import runpy
import sys


if __name__ == "__main__":
    # Run as a script: hand over to the real module's __main__.
    runpy.run_module("data_providers.jobs.data_ops.download_fill_contracts", run_name="__main__")
else:
    # Imported: ALIAS, do not copy. Replacing the sys.modules entry means `import download_fill_contracts`
    # returns the REAL module object, so identity, private names and reload all behave:
    #   * `importlib.reload(download_fill_contracts)` reloads the real module (check_iv_solvers.py relies on
    #     this to re-read BS_FORCE_PYTHON -- a copy-based shim silently ignored the reload);
    #   * `download_fill_contracts._SO` and friends stay reachable, which a `from ... import *` copy drops
    #     because it skips underscore-prefixed names;
    #   * there is no second, stale copy of module state to drift out of sync.
    import data_providers.jobs.data_ops.download_fill_contracts as _m
    sys.modules[__name__] = _m
