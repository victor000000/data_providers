"""The standalone suite never reads credentials or databases from a live runtime."""
import atexit
import os
from pathlib import Path
import shutil
import tempfile

_runtime = tempfile.TemporaryDirectory(prefix="data-providers-tests-")
atexit.register(_runtime.cleanup)
os.environ["DATA_PROVIDERS_HOME"] = _runtime.name
seed = Path(__file__).resolve().parents[1] / "data_providers/seed"
plans = Path(_runtime.name) / "plans"
plans.mkdir()
for path in seed.glob("*.csv"):
    shutil.copy2(path, plans / path.name)
