"""Bounded Eastmoney ETF entity-body receipts and strict daily-bar parsing."""
import hashlib
import json
import math
import fcntl
import os
from datetime import UTC, date, datetime
from pathlib import Path

import requests

ENDPOINT = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'


class ProtectedETFRequest(ValueError):
    """An existing failed, invalid or uncertain key must not issue HTTP again."""


def _write_durable(path, value, *, exclusive=False):
    with path.open('x' if exclusive else 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fetch(folder, *, start, end, adjustment, session=None):
    if adjustment not in {'raw', 'qfq'}:
        raise ValueError('Unsupported adjustment convention')
    # Explicit bounded instrument: the market ID is not guessed from an arbitrary ticker.
    params = {'fields1':'f1,f2,f3,f4,f5,f6', 'fields2':'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116',
              'klt':'101','fqt':'0' if adjustment == 'raw' else '1','beg':start.strftime('%Y%m%d'),
              'end':end.strftime('%Y%m%d'),'secid':'1.510050','ut':'7eea3edcaed734bea9cbfc24409ed989'}
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    request_key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    # This lock and ledger protect this folder only. Cross-folder semantic audits
    # remain required before allocating requests; changing folders is not a retry.
    with (folder / (request_key + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _fetch_locked(folder, request_key, params, start=start, end=end,
                             adjustment=adjustment, session=session)


def _fetch_locked(folder, request_key, params, *, start, end, adjustment, session):
    receipt_path = folder / (request_key + '.json')
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt.get('status') in {'in_flight', 'request_failed'} or not receipt.get('body_file'):
            raise ProtectedETFRequest('Existing failed or uncertain ETF request is protected')
        body = (folder / receipt['body_file']).read_bytes()
        if hashlib.sha256(body).hexdigest() != receipt['sha256']:
            raise ValueError('Cached source body checksum mismatch')
        if receipt['http_status'] == 200:
            try:
                parse(body,receipt,receipt_path,start=start,end=end,adjustment=adjustment)
            except (ValueError, KeyError, TypeError):
                raise ProtectedETFRequest('Existing invalid ETF response is protected')
            else:
                return body, receipt, receipt_path
        raise ProtectedETFRequest('Existing unsuccessful ETF response is protected')
    if any(folder.glob(request_key + '-*.json')):
        raise ProtectedETFRequest('Legacy ETF attempt evidence is protected; offline audit required')
    if isinstance(session, requests.Session):
        retries = session.get_adapter(ENDPOINT).max_retries
        if retries.total not in (0, False):
            raise ValueError('ETF acquisition requires automatic transport retries disabled')
    getter = session.get if session else requests.get
    stamp = datetime.now(UTC)
    intent = {'parameters': params, 'retrieved_at': stamp.isoformat(),
              'status': 'in_flight', 'automatic_retries': 0}
    _write_durable(receipt_path, intent, exclusive=True)
    try:
        response = getter(ENDPOINT, params=params, timeout=20)
    except requests.RequestException as exc:
        error = {**intent, 'status':'request_failed', 'error':type(exc).__name__}
        _write_durable(folder / (request_key + '-error-' + stamp.strftime('%Y%m%dT%H%M%S%fZ') + '.json'), error, exclusive=True)
        temporary = receipt_path.with_suffix('.tmp')
        _write_durable(temporary, error)
        temporary.replace(receipt_path)
        raise
    body = response.content
    sha = hashlib.sha256(body).hexdigest()
    body_path = folder / (sha + '.body')
    if body_path.exists() and body_path.read_bytes() != body:
        raise ValueError('Source receipt digest collision')
    body_path.write_bytes(body)
    receipt = {'parameters':params,'url':response.url,'http_status':response.status_code,
               'retrieved_at':stamp.isoformat(),'sha256':sha,'body_file':body_path.name}
    # Archive the original outcome; every outcome remains a protected key.
    _write_durable(folder / (request_key + '-' + stamp.strftime('%Y%m%dT%H%M%S%fZ') + '.json'), receipt, exclusive=True)
    temporary = receipt_path.with_suffix('.tmp')
    _write_durable(temporary, receipt)
    temporary.replace(receipt_path)
    response.raise_for_status()
    return body, receipt, receipt_path


def parse(body, receipt, receipt_path, *, start, end, adjustment):
    payload = json.loads(body)
    data = payload.get('data') if isinstance(payload,dict) else None
    if not isinstance(data,dict) or data.get('code') != '510050' or data.get('market') != 1:
        raise ValueError('Missing or mismatched ETF identity; not an empty successful history')
    params = receipt['parameters']
    if params.get('fqt') != ('0' if adjustment == 'raw' else '1'):
        raise ValueError('Adjustment receipt mismatch')
    rows, seen = [], set()
    stamp = datetime.fromisoformat(receipt['retrieved_at'])
    for text in data.get('klines', []):
        fields = text.split(',')
        if len(fields) != 11:
            raise ValueError('Unexpected ETF source field layout')
        day = date.fromisoformat(fields[0])
        op, close, high, low, volume, turnover = map(float, fields[1:7])
        if day in seen or not start <= day <= end:
            raise ValueError('Duplicate or out-of-window ETF session')
        if not all(math.isfinite(x) for x in (op,close,high,low,volume,turnover)):
            raise ValueError('Nonfinite ETF price/volume')
        if min(op,close,high,low) <= 0 or low > min(op,close) or high < max(op,close) or low > high:
            raise ValueError('Invalid ETF OHLC geometry')
        if volume < 0 or turnover < 0:
            raise ValueError('Negative ETF volume or turnover')
        seen.add(day)
        rows.append(('eastmoney_etf','SSE','CN:SSE:ETF:510050','510050','ETF',day,adjustment,
                     op,high,low,close,volume,'vendor_native_unverified',turnover,None,None,stamp,str(receipt_path)))
    if not rows:
        raise ValueError('No ETF history returned; coverage not established')
    return sorted(rows, key=lambda row:row[5])
