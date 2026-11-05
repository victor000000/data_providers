"""Runtime paths are independent of source code and consumer applications."""
import os
import time
import tomllib
from pathlib import Path


def data_home():
    value = os.environ.get("DATA_PROVIDERS_HOME")
    if value:
        return Path(value).expanduser().resolve()
    config = Path(os.environ.get(
        "DATA_PROVIDERS_CONFIG", Path(__file__).resolve().parents[1] / "config.local.toml"
    )).expanduser()
    if config.exists():
        with config.open("rb") as handle:
            value = tomllib.load(handle)["storage"]["home"]
        path = Path(value).expanduser()
        return (path if path.is_absolute() else config.parent / path).resolve()
    project = Path(__file__).resolve().parents[1]
    if (project / "pyproject.toml").is_file():
        return project / "runtime"
    return Path.home() / ".local" / "share" / "data-providers"


def trading_logs_dir():
    """External source exports are inputs, never resolved relative to storage."""
    value = os.environ.get("TRADING_LOGS_DIR")
    if value:
        return Path(value).expanduser().resolve()
    mounted = Path("/root/trading_logs")
    if mounted.is_dir():
        return mounted
    windows = Path("/mnt/c/Users/victo/Documents/trading_logs")
    if windows.is_dir():
        return windows
    return Path.home() / "trading_logs"


ROOT = data_home()

# Legacy ingestion stores naive UTC timestamps. Pin subprocesses consistently too.
os.environ["TZ"] = "UTC"
if hasattr(time, "tzset"):
    time.tzset()
