"""Archive the newest export of each filename type without interpreting executions."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from data_providers.runtime import data_home, trading_logs_dir

STAMP = re.compile(r'20\d{2}[-_]?(?:0[1-9]|1[0-2])[-_]?(?:0[1-9]|[12]\d|3[01])'
                   r'(?:[_-][0-2]\d[0-5]\d[0-5]\d(?!\d))?')


def export_kind(path):
    def strip_date(match):
        digits = re.sub(r'\D', '', match[0])
        try:
            datetime.strptime(digits[:8], '%Y%m%d')
        except ValueError:
            return match[0]
        return ''
    stem = re.sub(r'[\s_.-]+', '_', STAMP.sub(strip_date, path.stem)).strip('_')
    return (stem or 'export').casefold() + path.suffix.lower()


def archive(source, home):
    source, home = Path(source), Path(home)
    if not source.is_dir():
        raise FileNotFoundError(f'Trading export source is unavailable: {source}')
    selected = {}
    for path in source.iterdir():
        if not path.is_file() or path.name.startswith(('.', '~$')):
            continue
        kind = export_kind(path)
        if kind not in selected or (path.stat().st_mtime_ns, path.name) > (
                selected[kind].stat().st_mtime_ns, selected[kind].name):
            selected[kind] = path
    if not selected:
        raise FileNotFoundError('No trading export files found')
    stamp = datetime.now(timezone.utc)
    name = stamp.strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
    root = home / 'var/raw/trading_exports'
    pending = root / ('.incomplete-' + name)
    pending.mkdir(parents=True)
    manifest = {'captured_utc': stamp.isoformat(), 'source': str(source.resolve()), 'files': []}
    for kind, path in sorted(selected.items()):
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise RuntimeError(f'Source changed while capturing {path.name}; incomplete receipt retained')
        relative = Path(kind) / path.name
        target = pending / relative
        target.parent.mkdir()
        target.write_bytes(raw)
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        digest = hashlib.sha256(raw).hexdigest()
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise OSError(f'Receipt checksum mismatch: {path.name}')
        manifest['files'].append({'kind': kind, 'filename': path.name,
            'path': str(relative), 'bytes': len(raw), 'sha256': digest,
            'modified_utc': datetime.fromtimestamp(before.st_mtime, timezone.utc).isoformat()})
    (pending / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    destination = root / name
    pending.rename(destination)
    return destination, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    path, manifest = archive(trading_logs_dir(), data_home())
    print(json.dumps({'archive': str(path), 'export_types': len(manifest['files']),
                      'bytes': sum(f['bytes'] for f in manifest['files'])}))


if __name__ == '__main__':
    main()
