"""Immutable Schwab export observations; a file mtime never establishes timezone."""
import csv
import datetime as dt
import hashlib
import re

from data_providers.data.schema import broker_snapshots


def store(con, path, positions, account):
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    rows = list(csv.reader(raw.decode('utf-8-sig').splitlines()))
    header = rows[0][0] if rows and rows[0] else ''
    match = re.search(r'Position Statement for (\S+).* on (\d{1,2}/\d{1,2}/\d{2} \d{1,2}:\d{2}:\d{2})', header)
    if not match or match[1] != account:
        raise ValueError('Missing or mismatched Schwab snapshot account/source clock')
    local = dt.datetime.strptime(match[2], '%m/%d/%y %H:%M:%S')
    broker_snapshots.ensure(con)
    saved = con.execute('SELECT raw_content FROM broker_snapshot_receipts WHERE receipt_sha256=? AND account=?', [digest, account]).fetchone()
    if saved:
        if saved[0] != raw:
            raise ValueError('Snapshot hash collision or damaged receipt')
        return digest, False
    con.execute('BEGIN')
    try:
        con.execute('INSERT INTO broker_snapshot_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    [digest, account, 'Schwab', 'schwab_position_statement', path.name,
                     local, None, None, dt.datetime.now(dt.UTC), None, False,
                     'parsed_nonzero_option_rows', raw])
        if positions:
            con.executemany('INSERT INTO broker_snapshot_positions VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                            [[digest, *row[1:]] for row in positions])
        con.execute('COMMIT')
    except Exception:
        con.execute('ROLLBACK')
        raise
    return digest, True
