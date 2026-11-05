"""Atomic, private job receipts and kernel-lock inspection (never remove locks)."""
import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from ..runtime import data_home


def now():
    return datetime.now(timezone.utc).isoformat()


def root(home=None):
    return Path(home or data_home()) / 'var/maintenance/web-control'


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(data, handle, allow_nan=False, default=str)
    temporary.replace(path)


def held(path):
    try:
        with Path(path).open('r') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
    except FileNotFoundError:
        return False
    except OSError:
        return True  # Cannot prove a safe writer boundary.
    return False


def blockers(home=None):
    home = Path(home or data_home())
    folder = home / 'var/maintenance'
    paths = list(folder.rglob('*.lock')) + [home / 'lake/publish.lock']
    return [str(p.relative_to(home)) for p in paths
            if p.name != 'watcher.lock' and 'web-control' not in p.parts and held(p)]
