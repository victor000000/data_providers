"""Read provider receipts without opening mutable canonical databases."""
from pathlib import Path
from ..runtime import data_home
from .health_sources import SOURCES
from .jobs import history
from .state import blockers, held, now, read, root
FIELDS = ('status','updated_utc','observed_utc','started_utc','completed_symbols',
          'total_symbols','requests','staged_rows','errors','symbol','completed','total',
          'rows','verified_rows','attempted','failed','phase')


def existing_jobs(home):
    folder = home/'var/maintenance'
    paths = sorted(folder.glob('**/status.json'), key=lambda p:p.stat().st_mtime, reverse=True)
    rows = []
    for path in paths:
        if 'web-control' in path.parts:
            continue
        doc = read(path)
        if not any(k in doc for k in ('status','phase','requests','completed')):
            continue
        row = {k:doc[k] for k in FIELDS if isinstance(doc.get(k),(str,int,float,bool))}
        row.update(name=str(path.parent.relative_to(folder)), receipt=str(path.relative_to(home)),
                   worker_lock_held=held(path.parent/'worker.lock'), receipt_mtime=path.stat().st_mtime)
        rows.append(row)
        if len(rows) == 30:
            break
    return rows


def status(home=None):
    home = Path(home or data_home())
    evidence = read(root(home)/'sources.json')
    known = {r['source']:r for r in evidence.get('sources',[])}
    sources = [known.get(n,dict(source=n,transport='not checked',access='not verified',
                                quote_status='not checked',checked_at=None,detail='Run source checks')) for n in SOURCES]
    current = home/'lake/CURRENT'
    release = current.read_text().strip() if current.exists() else None
    manifest = read(home/'lake/snapshots'/release/'manifest.json') if release else {}
    monitor = read(home/'var/maintenance/consumer-monitor/status.json')
    return dict(received_at=now(), sources=sources, jobs=history(home), existing_jobs=existing_jobs(home),
                blockers=blockers(home), release=release, published_at=manifest.get('created_utc'),
                monitor_at=monitor.get('updated_utc',monitor.get('observed_utc')),
                scope='US + mainland CN + trading logs; no HK acquisition',
                source_note='Connectivity is not live quote freshness. HTTP probes do not establish entitlement.',
                market_note='Exchange/session timestamps remain on Option chain and CN 市场.',
                exports_scan=_scan(home))


def _scan(home):
    doc = read(home/'var/maintenance/trading-inputs/latest.json')
    result = {k:v for k,v in doc.items() if k in ('observed_utc','scanned_at','status','available','scanned_utc',
                                               'file_count','changed_count','added_count','removed_count')}
    for key in ('files','added','changed','removed'):
        if isinstance(doc.get(key), list):
            result[key + '_count'] = len(doc[key])
    return result
