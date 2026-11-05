"""Persist each bounded probe independently, including unavailable sources."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from ..runtime import trading_logs_dir
from .health_sources import HTTP, SOURCES, alpaca, http_check, local_check
from .state import now, read, root, write


def probe(name):
    try:
        if name in HTTP:
            result = http_check(HTTP[name])
        elif name.startswith('Alpaca'):
            result = alpaca(name == 'Alpaca paper')
        elif name == 'Trading exports':
            path = trading_logs_dir()
            files = [p for p in path.iterdir() if p.is_file()]
            result = dict(transport='reachable', access='mounted folder', quote_status='not applicable',
                          detail=f'{len(files)} files; maintenance rescans contents and provenance')
        else:
            result = local_check(name)
    except Exception as exc:
        # Never serialize exception URLs, request headers, response bodies or credentials.
        result = dict(transport='unavailable', access='not verified', quote_status='not checked',
                      detail=type(exc).__name__ + '; probe failed')
    return dict(source=name, checked_at=now(), **result)


def cached_probe(name, previous):
    row = previous.get(name, {})
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row['checked_at'])).total_seconds()
    except (KeyError, ValueError, TypeError):
        age = 900
    if row.get('access') == 'denied' and 0 <= age < 900:
        return dict(row, cache_note='Entitlement denial retained for 15 minutes; no repeated API request')
    return probe(name)


def probe_all(progress, home=None):
    rows = []
    previous = {r['source']:r for r in read(root(home)/'sources.json').get('sources',[])}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(cached_probe, name, previous) for name in SOURCES]
        for future in as_completed(futures):
            rows.append(future.result())
            ordered = sorted(rows, key=lambda r:SOURCES.index(r['source']))
            write(root(home)/'sources.json', dict(checked_at=now(), sources=ordered))
            progress(ordered)
    return rows
