"""Launch fixed provider operations; no shell commands or paths from the browser."""
import fcntl
import hashlib
import json
import os
import re
import subprocess
from dataclasses import asdict
from pathlib import Path
from ..maintenance import plan
from ..runtime import data_home
from .state import blockers, held, now, read, root, write
ACTIONS = {'all', 'us', 'cn', 'trades', 'verify', 'health'}
PROJECT = Path(__file__).resolve().parents[2]


def preview(action):
    if action not in ACTIONS:
        raise ValueError('Unknown maintenance action')
    steps = [asdict(s) for s in plan(action)] if action not in {'verify','health'} else []
    data = dict(action=action, steps=steps, publish=bool(steps),
                scope='US + CN + trading logs' if action == 'all' else action)
    data['digest'] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return data


def start(action, request_id, digest, home=None):
    if preview(action)['digest'] != digest or not isinstance(request_id, str) or not re.fullmatch(r'[a-f0-9]{32}', request_id):
        raise ValueError('Invalid request identifier or changed plan; review again')
    folder = root(home)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder/'dispatch.lock').open('a') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        return _start(action, request_id, digest, home)


def _start(action, request_id, digest, home=None):
    spec = preview(action)
    if digest != spec['digest']:
        raise ValueError('Plan changed; preview this update again')
    if not isinstance(request_id, str) or not re.fullmatch(r'[a-f0-9]{32}', request_id):
        raise ValueError('Expected a unique request identifier')
    home = Path(home or data_home())
    folder = root(home)
    folder.mkdir(parents=True, exist_ok=True)
    record = folder / (request_id + '.json')
    lock = (folder / ('health.lock' if action == 'health' else 'job.lock')).open('a')
    try:
        previous = read(record)
        if previous:
            if previous.get('action') != action:
                raise ValueError('Request identifier already belongs to a different action')
            return previous
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('An operation is already active; follow its progress below') from exc
        if action not in {'health','verify'} and blockers(home):
            raise ValueError('Provider acquisition/publication is active; follow existing jobs before updating')
        lock.seek(0)
        lock.truncate()
        lock.write(request_id)
        lock.flush()
        job = dict(id=request_id, action=action, status='queued', created_at=now(), updated_at=now(),
                   completed=0, total=len(spec['steps']), steps=[], plan_digest=digest)
        write(record, job)
        try:
            with (folder / (request_id+'.log')).open('ab') as output:
                os.chmod(output.name, 0o600)
                proc = subprocess.Popen([str(PROJECT/'scripts/native.sh'), 'python', '-m',
                    'data_providers.web_ops.runner', request_id, str(lock.fileno())], cwd=PROJECT,
                    env=dict(os.environ, DATA_PROVIDERS_HOME=str(home)), stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
                    pass_fds=(lock.fileno(),))
            # Child owns this same open-file lock through process exit; web restart is safe.
            import threading
            threading.Thread(target=proc.wait, daemon=True).start()
        except OSError:
            job.update(status='failed', error='Provider worker could not start', updated_at=now())
            write(record, job)
        return job
    finally:
        lock.close()


def history(home=None):
    folder = root(home)
    rows = []
    for path in sorted(folder.glob('*.json'), key=lambda p:p.stat().st_mtime, reverse=True)[:30]:
        job = read(path)
        if not job.get('id'):
            continue
        key = 'health.lock' if job.get('action') == 'health' else 'job.lock'
        if job.get('status') in {'queued','running','publishing'} and (not held(folder/key) or (folder/key).read_text() != job['id']):
            job = dict(job, status='interrupted', error='Worker lock released; inspect provider receipts before retrying')
        rows.append(job)
    return rows
