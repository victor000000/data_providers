"""Deliver completed discovery receipts while the separate price backfill continues."""
import json
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

from data_providers.jobs.history.futures_store import check, merge, open_stage, upsert
from data_providers.jobs.history.maintain_catalogs import VENUES
from data_providers.lake import digest
from data_providers.runtime import data_home


def product_rows(items, receipt):
    groups = defaultdict(list)
    for item in items:
        venue = VENUES.get(item.get('trading_venue'), item.get('trading_venue') or 'UNSPECIFIED')
        groups[(item['product_code'], venue, item['date'])].append(item)
    rows = []
    for (code, venue, day), variants in sorted(groups.items()):
        # Product code is a family: a source can return single and combo variants.
        # Only uncontested values belong at the family's top metadata level.
        keys = set().union(*(item.keys() for item in variants))
        conflicts = [key for key in sorted(keys) if len({json.dumps(x.get(key), sort_keys=True) for x in variants}) > 1]
        details = {key: variants[0][key] for key in keys - set(conflicts) if key in variants[0]}
        details['_definition_variants'] = variants
        details['_provider_coverage'] = {
            'receipt': receipt, 'venue_scope': 'US_listed' if venue in {'CME','CBOT','COMEX','NYMEX'} else 'unknown',
            'optionability': 'unknown_until_actual_option_inventory_evidence',
            'price_iv_history_complete': False, 'active_status': 'unknown; catalog as-of date only',
            'definition_count': len(variants), 'conflicting_metadata_fields': conflicts,
            'missing_feeds': ['ICE and venues outside configured Massive feed']}
        rows.append(('massive', code, venue, date.fromisoformat(day),
                     variants[0]['name'], json.dumps(details)))
    return rows


def build(home=None):
    home = Path(home) if home else data_home()
    run = datetime.now(UTC).strftime('catalog-progress-%Y%m%dT%H%M%S%fZ')
    source = home / 'var/maintenance/discovery-20261017/raw'
    rows, receipts = [], []
    for path in sorted(source.glob('*.json')):
        payload = json.loads(path.read_text())
        items = payload.get('results', [])
        if not items or 'product_code' not in items[0] or 'ticker' in items[0]:
            continue
        receipt = {'path': str(path), 'sha256': digest(path), 'rows': len(items),
                   'continuation_present': bool(payload.get('next_url'))}
        receipts.append(receipt)
        rows.extend(product_rows(items, receipt))
    if not rows:
        raise ValueError('No saved provider product discovery receipts')
    path, stage = open_stage(home, run)
    try:
        stage.execute('BEGIN')
        upsert(stage, 'us_futures_products', rows)
        check(stage, run, 'ALL', 'product_catalog_progress', 'observed', len(rows),
              detail='Discovery only; price/IV completeness and optionability are independent')
        check(stage, run, 'ICE', 'product_catalog', 'blocked', detail='Outside configured provider feed')
        stage.execute('COMMIT')
    finally:
        stage.close()
    proof = merge(home, path)
    folder = home / 'var/maintenance' / run
    folder.mkdir()
    report = {'status': 'verified_catalog_progress', 'products': len(rows),
              'source_definitions': sum(r['rows'] for r in receipts), 'receipts': receipts,
              'verified_merge': proof, 'prices_complete': False, 'optionability_complete': False,
              'worldwide_or_unspecified_venue_classification': 'unknown; never silently classified US'}
    (folder / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return folder, report


if __name__ == '__main__':
    folder, report = build()
    print(json.dumps({'path': str(folder), 'products': report['products']}))
