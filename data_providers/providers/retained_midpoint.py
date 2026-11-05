"""Normalize retained TWS MIDPOINT bars without treating them as trades or NBBO."""
import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def aware(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('An explicit timestamp timezone is required')
    return result.astimezone(UTC)


def normalize(payload, *, metadata_payload=None):
    data = json.loads(payload)
    if data.get('kind') != 'MIDPOINT':
        raise ValueError('Only source-labelled MIDPOINT receipts are supported')
    seconds = {'5 mins': 300, '1 hour': 3600}.get(data.get('bar_size'))
    if seconds is None:
        raise ValueError('Unsupported or missing interval')
    original_contract = data['contract']
    contract = dict(original_contract)
    metadata_sha = None
    if metadata_payload is not None:
        reference = json.loads(metadata_payload)['contract']
        for key in ('conId', 'secType', 'symbol', 'lastTradeDateOrContractMonth', 'strike', 'right'):
            if reference.get(key) != contract.get(key):
                raise ValueError('Metadata identity differs from observed contract')
        for key in ('currency', 'multiplier'):
            if contract.get(key) and str(contract[key]) != str(reference.get(key)):
                raise ValueError('Conflicting contract metadata')
            contract[key] = reference.get(key)
        metadata_sha = hashlib.sha256(metadata_payload).hexdigest()
    if contract.get('secType') != 'OPT' or contract.get('currency') != 'USD' or not contract.get('conId'):
        raise ValueError('Exact US option contract identity required')
    multiplier = float(contract['multiplier'])
    strike = float(contract['strike'])
    if not math.isfinite(multiplier) or multiplier <= 0 or not math.isfinite(strike) or strike <= 0 or contract['right'] not in ('C', 'P'):
        raise ValueError('Invalid contract terms')
    captured = aware(data['received_utc'])
    expiry = datetime.strptime(contract['lastTradeDateOrContractMonth'], '%Y%m%d').date()
    sha = hashlib.sha256(payload).hexdigest()
    bars = data['bars']
    stamps = [aware(b['date']) for b in bars]
    if stamps != sorted(set(stamps)):
        raise ValueError('Duplicate or unordered bar timestamps')
    rows = []
    for i, (bar, start) in enumerate(zip(bars, stamps)):
        values = [float(bar[k]) for k in ('open', 'high', 'low', 'close')]
        o, h, l, c = values
        if not all(math.isfinite(v) for v in values) or not 0 <= l <= min(o, c) <= max(o, c) <= h:
            raise ValueError('Invalid midpoint OHLC')
        day = start.astimezone(ZoneInfo('America/New_York')).date()
        if day > expiry or start > captured:
            raise ValueError('Observation outside contract/capture bounds')
        # The initial hourly RTH bar can be shorter than one hour.
        end = None
        basis = 'unknown_last_or_gapped_interval'
        if i + 1 < len(stamps):
            following = stamps[i + 1]
            if following.astimezone(ZoneInfo('America/New_York')).date() == day and following - start <= timedelta(seconds=seconds):
                end, basis = following, 'inferred_from_next_observed_bar_start'
        rows.append(('ibkr_tws', 'IBKR:' + str(contract['conId']), contract['symbol'], expiry,
                     strike, contract['right'], multiplier, contract['currency'], day, start, end,
                     seconds, basis, 'MIDPOINT', o, h, l, c, None, None, None, captured, sha,
                     json.dumps({'contract': original_contract, 'resolved_contract': contract,
                                 'metadata_receipt_sha256': metadata_sha, 'bar': bar}, sort_keys=True)))
    return rows
