"""Verify retained Yahoo receipts and deliver typed capitalization/listing observations.

Fund assets and company capitalization remain distinct. Retrieval is not the
unknown effective date of vendor fundamentals, nor historical availability.
"""
import fcntl
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from data_providers.lake import literal
from data_providers.runtime import data_home

DDL = {
    'capitalization_observations': '''symbol VARCHAR, source VARCHAR, retrieved_at TIMESTAMPTZ,
        metric VARCHAR CHECK(metric IN ('fund_total_assets','equity_market_cap')),
        value DOUBLE CHECK(isfinite(value) AND value>0), currency VARCHAR,
        effective_date DATE, historical_available_at TIMESTAMPTZ,
        receipt_path VARCHAR, receipt_sha256 VARCHAR,
        PRIMARY KEY(symbol,source,retrieved_at,metric)''',
    'option_listing_observations': '''underlying VARCHAR, source VARCHAR, retrieved_at TIMESTAMPTZ,
        contract_symbol VARCHAR, expiry DATE, strike DOUBLE, call_put VARCHAR,
        currency VARCHAR, receipt_path VARCHAR, receipt_sha256 VARCHAR,
        PRIMARY KEY(source,contract_symbol,retrieved_at)''',
}


def capitalization(symbol, info, retrieved_at, path, sha):
    kind = info.get('quoteType')
    field, metric = {'ETF': ('totalAssets', 'fund_total_assets'),
                     'MUTUALFUND': ('totalAssets', 'fund_total_assets'),
                     'EQUITY': ('marketCap', 'equity_market_cap')}.get(kind, (None, None))
    value = info.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return (symbol, 'yahoo_quote_summary', retrieved_at, metric, value, info.get('currency'),
            None, None, str(path), sha)


def build(folder, home=None):
    folder, home = Path(folder), Path(home or data_home())
    probe = json.loads((folder / 'yahoo-probe.json').read_text())
    caps, listings = [], {}
    iv_rows = []
    iv_path = folder / 'ibkr-iv-probe.json'
    if iv_path.exists():
        iv_receipt = json.loads(iv_path.read_text())
        if iv_receipt.get('source') != 'ibkr_tws30d':
            raise ValueError('Unexpected IV source')
        for symbol, record in iv_receipt.get('symbols', {}).items():
            if record.get('status') != 'observed':
                continue
            for wrapped in record['bars']:
                bar = wrapped['BarData']
                value = bar['close']
                day = datetime.fromisoformat(bar['date']).date()
                if not math.isfinite(value) or not 0 < value < 5 or day.weekday() >= 5:
                    raise ValueError('Invalid IV observation; investigate receipt')
                iv_rows.append((day, symbol, None, value, None, 'ibkr_tws30d'))
    for symbol, record in probe.items():
        path = folder / (symbol + '-info.json')
        if not path.exists():
            continue
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if sha != record['info_sha256']:
            raise ValueError('Info receipt hash mismatch: ' + symbol)
        row = capitalization(symbol, json.loads(raw), record['retrieved_at'], path, sha)
        if row:
            caps.append(row)
    for line in (folder / 'http.jsonl').read_text().splitlines():
        receipt = json.loads(line)
        if '/finance/options/' not in receipt['endpoint'] or receipt['status'] != 200:
            continue
        path = folder / (receipt['sha256'] + '.body')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != receipt['sha256']:
            raise ValueError('HTTP receipt hash mismatch')
        for result in json.loads(raw).get('optionChain', {}).get('result') or []:
            symbol = result.get('underlyingSymbol')
            if symbol not in probe:
                continue
            for chain in result.get('options') or []:
                for side, cp in (('calls', 'C'), ('puts', 'P')):
                    for contract in chain.get(side) or []:
                        expiry = datetime.fromtimestamp(contract['expiration'], UTC).date()
                        observed = datetime.fromisoformat(receipt['retrieved_at'])
                        if expiry < observed.date():
                            continue
                        key = (contract['contractSymbol'], receipt['retrieved_at'])
                        listings[key] = (symbol, 'yahoo_option_chain', observed, key[0], expiry,
                                         contract['strike'], cp, contract.get('currency'),
                                         str(path), receipt['sha256'])
    stage = home / 'staging/capitalization' / 'gt50-20261018.duckdb'
    stage.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(stage)) as con:
        con.execute("SET TimeZone='UTC'")
        con.execute('BEGIN')
        for table, ddl in DDL.items():
            con.execute(f'CREATE TABLE IF NOT EXISTS {table} ({ddl})')
        con.execute('''CREATE TABLE IF NOT EXISTS iv_history (
            day DATE,underlying VARCHAR,expiry DATE,atm_iv DOUBLE,spot DOUBLE,source VARCHAR,
            PRIMARY KEY(day,underlying,source))''')
        if iv_rows:
            con.executemany('INSERT OR REPLACE INTO iv_history VALUES (?,?,?,?,?,?)', iv_rows)
        for table, rows in [('capitalization_observations', caps),
                            ('option_listing_observations', list(listings.values()))]:
            if rows:
                con.executemany(f"INSERT OR REPLACE INTO {table} VALUES ({','.join('?' for _ in rows[0])})", rows)
        con.execute('COMMIT')
    with (home / 'var/maintenance/writer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with duckdb.connect(str(home / 'market_history.duckdb')) as con:
            con.execute("SET TimeZone='UTC'")
            con.execute(f'ATTACH {literal(stage)} AS staged (READ_ONLY)')
            con.execute('BEGIN')
            try:
                for table, ddl in DDL.items():
                    con.execute(f'CREATE TABLE IF NOT EXISTS {table} ({ddl})')
                    con.execute(f'INSERT OR REPLACE INTO {table} SELECT * FROM staged.{table}')
                    if con.execute(f'SELECT count(*) FROM (SELECT * FROM staged.{table} EXCEPT SELECT * FROM {table})').fetchone()[0]:
                        raise ValueError('Verified merge failed: ' + table)
                # A short freshness probe must never replace a healthy long history
                # or silently rewrite vendor revisions to existing measurements.
                con.execute('CREATE TEMP TABLE missing_iv AS SELECT * FROM staged.iv_history s '
                            'WHERE NOT EXISTS (SELECT 1 FROM iv_history l WHERE l.day=s.day '
                            'AND l.underlying=s.underlying AND l.source=s.source)')
                added_iv = con.execute('SELECT underlying,count(*),max(day) FROM missing_iv GROUP BY underlying').fetchall()
                con.execute('INSERT INTO iv_history SELECT * FROM missing_iv')
                if con.execute('SELECT count(*) FROM (SELECT * FROM missing_iv EXCEPT SELECT * FROM iv_history)').fetchone()[0]:
                    raise ValueError('IV verified merge failed')
                con.execute('COMMIT')
            except Exception:
                con.execute('ROLLBACK')
                raise
    report = {'capitalization_rows': len(caps), 'option_listing_rows': len(listings),
              'iv_missing_dates_added': [(symbol, count, str(day)) for symbol, count, day in added_iv],
              'iv_receipt_sha256': hashlib.sha256(iv_path.read_bytes()).hexdigest() if iv_path.exists() else None,
              'option_underlyings': sorted({r[0] for r in listings.values()}),
              'stage': str(stage), 'status': 'verified_merged',
              'effective_date': 'unknown; retrieved_at is observation time only'}
    (folder / 'delivery.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder')
    print(json.dumps(build(parser.parse_args().folder)))
