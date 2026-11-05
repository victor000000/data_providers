"""Plan new option dates without confusing existing expiry coverage with freshness.

This module never infers past request coverage from stored bars. Callers must supply
an audited request frontier and blocked contracts, plus completed venue sessions.
Acquisition is opt-in through an audited admission; publication remains separate.
"""
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path


def plan_tail(contracts, completed_sessions, *, attempted_through, blocked_symbols=(), receipts=()):
    """One contract/session per task; every prior receipt status excludes its interval.

Contracts carry source, symbol, expiry and last_stored_day. The explicit global
request frontier protects old failed/empty/uncertain requests that predate this
ledger. It is an audited acquisition bound, not the latest nonempty bar date.
"""
    if not isinstance(attempted_through, date):
        raise ValueError('An audited attempted-through date is required')
    sessions = sorted(set(completed_sessions))
    if any(not isinstance(d, date) for d in sessions):
        raise ValueError('Completed sessions must be dates')
    blocked = set(blocked_symbols)
    intervals = {}
    for receipt in receipts:
        p = receipt['params']
        start, end = date.fromisoformat(p['start']), date.fromisoformat(p['end_exclusive'])
        if end <= start:
            raise ValueError('Invalid receipt interval')
        identity = (p['source'], p['symbol'])
        intervals.setdefault(identity, []).append((start, end))
        if receipt.get('status') in ('denied', 'refused'):
            blocked.add(p['symbol'])
    tasks = {}
    for contract in contracts:
        source, symbol = contract['source'], contract['symbol']
        if source != 'alpaca':
            raise ValueError('This planner is only for Alpaca option trade bars')
        if symbol in blocked:
            continue
        last, expiry = contract['last_stored_day'], contract['expiry']
        if not isinstance(last, date) or not isinstance(expiry, date):
            raise ValueError('Existing contracts require observed dates and expiry')
        for session in sessions:
            if session <= max(last, attempted_through) or session > expiry:
                continue
            if any(start <= session < end for start, end in intervals.get((source, symbol), [])):
                continue
            params = dict(source=source, symbol=symbol, start=session.isoformat(),
                          end_exclusive=(session + timedelta(days=1)).isoformat(),
                          timeframe='1Day', data_type='option_trade_bars')
            key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
            tasks[key] = dict(key=key, params=params)
    return sorted(tasks.values(), key=lambda t: (t['params']['start'], t['params']['symbol']))


def claim_request(directory, task):
    """Durably record intent before network I/O; existing keys may never be replayed.

Caller must hold its worker lock and re-plan against all receipts before claiming.
A crash leaves an in-flight receipt which the planner treats as attempted.
"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    expected = hashlib.sha256(json.dumps(task['params'], sort_keys=True).encode()).hexdigest()
    if task['key'] != expected:
        raise ValueError('Request key does not match parameters')
    path = directory / (expected + '.json')
    with path.open('x') as stream:
        json.dump({**task, 'status': 'in_flight'}, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def _replace_receipt(path, receipt):
    """Finish only this claimed request; a crash before replace retains its intent."""
    temp = path.with_suffix('.pending')
    with temp.open('x') as stream:
        json.dump(receipt, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fetch_once(params):
    """Use the existing paced transport, explicitly disabling HTTP retries."""
    from urllib.parse import urlencode
    from data_providers.credentials import AlpacaHeaders
    from data_providers.providers.alpaca_http import get_json, utc_day_bounds
    end = date.fromisoformat(params['end_exclusive']) - timedelta(days=1)
    start, end = utc_day_bounds(params['start'], end.isoformat())
    url = 'https://data.alpaca.markets/v1beta1/options/bars?' + urlencode(
        dict(symbols=params['symbol'], timeframe='1Day', start=start, end=end, limit=10000))
    return get_json(url, AlpacaHeaders(), tries=1)


def acquire_task(directory, task, fetch=fetch_once):
    """Acquire one admitted contract/day with an immutable exclusion receipt.

The caller holds the worker lock and re-plans against all receipts first. No
positions, executions, underlying refresh or canonical writes occur here.
"""
    from datetime import datetime, timezone
    import math
    import time
    p = task['params']
    start, end = date.fromisoformat(p['start']), date.fromisoformat(p['end_exclusive'])
    if p.get('source') != 'alpaca' or p.get('timeframe') != '1Day' or end-start != timedelta(days=1):
        raise ValueError('Only one-day Alpaca option tasks may be acquired')
    if p.get('data_type') != 'option_trade_bars':
        raise ValueError('Unexpected request data type')
    path = claim_request(directory, task)
    receipt = {**task, 'status': 'in_flight', 'requested_utc': datetime.now(timezone.utc).isoformat()}
    tick = time.monotonic()
    try:
        payload = fetch(dict(p))
        raw = json.dumps(payload, sort_keys=True, allow_nan=False).encode()
        digest = hashlib.sha256(raw).hexdigest()
        raw_path = path.parent / (task['key'] + '-' + digest + '.raw.json')
        with raw_path.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        receipt.update(raw_file=str(raw_path), raw_sha256=digest, raw_format='parsed_response_json')
        if not isinstance(payload, dict) or 'bars' not in payload:
            raise ValueError('Missing bars envelope; cannot classify as empty')
        if payload.get('next_page_token'):
            raise ValueError('Unexpected pagination for a single daily contract; preserved without replay')
        by_symbol = payload['bars'] or {}
        if not isinstance(by_symbol, dict) or set(by_symbol)-{p['symbol']}:
            raise ValueError('Unexpected response contract')
        bars = by_symbol.get(p['symbol']) or []
        if not isinstance(bars, list) or len(bars) > 1:
            raise ValueError('Unexpected daily bar count')
        for bar in bars:
            stamp = datetime.fromisoformat(bar['t'].replace('Z', '+00:00'))
            if stamp.tzinfo is None or stamp.astimezone(timezone.utc).date() != start:
                raise ValueError('Bar timestamp outside requested UTC day')
            values = [float(bar[k]) for k in ('o','h','l','c','v')]
            o,h,l,c,v = values
            if not all(math.isfinite(x) for x in values) or not (0 < l <= min(o,c) <= max(o,c) <= h) or v < 0:
                raise ValueError('Invalid daily OHLCV; retained for review')
            if bar.get('vw') is not None and (not math.isfinite(float(bar['vw'])) or float(bar['vw']) <= 0):
                raise ValueError('Invalid VWAP')
        receipt.update(status='downloaded' if bars else 'empty', rows=len(bars))
    except Exception as exc:
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        receipt.update(status='denied' if status in (401,403) else 'failed_no_replay',
                       error_type=type(exc).__name__, http_status=status)
        # Avoid saving exception URLs or credential-bearing transport text.
    receipt.update(elapsed_seconds=round(time.monotonic()-tick,3),
                   finished_utc=datetime.now(timezone.utc).isoformat())
    _replace_receipt(path, receipt)
    return receipt


def stage_receipt(stage, receipt, contract):
    """Stage a verified response using an existing exact contract identity."""
    import duckdb
    if receipt['status'] != 'downloaded':
        return 0
    if contract['symbol'] != receipt['params']['symbol'] or contract['source'] != receipt['params']['source']:
        raise ValueError('Contract identity mismatch')
    raw = Path(receipt['raw_file']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != receipt['raw_sha256']:
        raise ValueError('Raw response hash mismatch')
    bars = json.loads(raw)['bars'][contract['symbol']]
    rows = [(contract['source'],contract['symbol'],contract['underlying'],contract['expiry'],
             contract['strike'],contract['call_put'],date.fromisoformat(receipt['params']['start']),
             b['o'],b['h'],b['l'],b['c'],b['v'],b.get('vw')) for b in bars]
    with duckdb.connect(str(stage)) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS option_bars (
            source VARCHAR,symbol VARCHAR,underlying VARCHAR,expiry DATE,strike DOUBLE,
            call_put VARCHAR,day DATE,open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,
            volume DOUBLE,vwap DOUBLE,PRIMARY KEY(source,symbol,day))''')
        c.execute('CREATE TEMP TABLE incoming AS SELECT * FROM option_bars LIMIT 0')
        if rows:
            c.executemany('INSERT INTO incoming VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
        conflicts=c.execute('''SELECT count(*) FROM (SELECT i.* FROM incoming i
            JOIN option_bars b USING(source,symbol,day) EXCEPT ALL SELECT * FROM option_bars)''').fetchone()[0]
        if conflicts:
            raise ValueError('Conflicting staged option bars; existing data retained')
        c.execute('INSERT INTO option_bars SELECT * FROM incoming ON CONFLICT DO NOTHING')
        assert c.execute('SELECT count(*) FROM (SELECT * FROM incoming EXCEPT ALL SELECT * FROM option_bars)').fetchone()[0]==0
    return len(rows)


def merge_stage(home, stage):
    """Append a stopped worker's stage under the shared canonical writer lock."""
    import duckdb
    import fcntl
    from data_providers.lake import literal
    home,stage=Path(home),Path(stage)
    with (home/'var/maintenance/writer.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with duckdb.connect(str(home/'market_history.duckdb')) as c:
            c.execute('ATTACH '+literal(stage)+' AS append_stage (READ_ONLY)')
            c.execute('BEGIN')
            try:
                cols=[r[1] for r in c.execute("PRAGMA table_info('option_bars')").fetchall()]
                expected=['source','symbol','underlying','expiry','strike','call_put','day','open','high','low','close','volume','vwap']
                if cols != expected:
                    raise ValueError('Canonical option-bar schema changed')
                conflicts=c.execute('''SELECT count(*) FROM (SELECT s.* FROM append_stage.option_bars s
                    JOIN main.option_bars t USING(source,symbol,day) EXCEPT ALL SELECT * FROM main.option_bars)''').fetchone()[0]
                if conflicts:
                    raise ValueError('Canonical option-bar conflict; no overwrite permitted')
                new=c.execute('''SELECT count(*) FROM append_stage.option_bars s WHERE NOT EXISTS
                    (SELECT 1 FROM main.option_bars t WHERE s.source=t.source AND s.symbol=t.symbol AND s.day=t.day)''').fetchone()[0]
                c.execute('INSERT INTO main.option_bars SELECT * FROM append_stage.option_bars ON CONFLICT DO NOTHING')
                assert c.execute('SELECT count(*) FROM (SELECT * FROM append_stage.option_bars EXCEPT ALL SELECT * FROM main.option_bars)').fetchone()[0]==0
                c.execute('COMMIT')
            except Exception:
                c.execute('ROLLBACK')
                raise
    return {'new_rows':new,'exact_merge':True,'stage_retained':True}


def run_append(home, admission, *, execute=False, max_requests=10, fetch=fetch_once):
    """Execute only an explicitly audited, fixed contract/session admission.

An admission must include source-file hashes for the legacy request audit and
completed-session calendar. It is not inferred from nonempty canonical rows.
"""
    import fcntl
    home=Path(home)
    if admission.get('market')!='US' or admission.get('source')!='alpaca':
        raise ValueError('Admission must select US Alpaca option bars')
    if admission.get('legacy_request_audit_complete') is not True:
        raise ValueError('Legacy request audit is incomplete; acquisition blocked')
    for kind in ('request_audit','completed_session_evidence'):
        evidence=admission[kind]
        raw=Path(evidence['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=evidence['sha256']:
            raise ValueError('Admission evidence changed: '+kind)
    if max_requests < 1:
        raise ValueError('Positive request bound required')
    contracts=[]
    for c in admission['contracts']:
        contracts.append({**c,'expiry':date.fromisoformat(c['expiry']),
                          'last_stored_day':date.fromisoformat(c['last_stored_day'])})
    identities={(c['source'],c['symbol']):c for c in contracts}
    if len(identities)!=len(contracts):
        raise ValueError('Duplicate admitted contract identity')
    root=home/'var/maintenance/option-daily-append';root.mkdir(parents=True,exist_ok=True)
    with (root/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        receipts=[json.loads(p.read_text()) for p in root.glob('*.json') if len(p.stem)==64]
        prior_refusal=any(r.get('status') in ('denied','refused') for r in receipts)
        # Reconcile against current canonical rows, not a stale admission snapshot.
        import duckdb
        with duckdb.connect(str(home/'market_history.duckdb'),read_only=True) as db:
            for contract in contracts:
                existing=db.execute('''SELECT underlying,expiry,strike,call_put,max(day)
                    FROM option_bars WHERE source=? AND symbol=? GROUP BY 1,2,3,4''',
                    [contract['source'],contract['symbol']]).fetchall()
                expected=(contract['underlying'],contract['expiry'],contract['strike'],contract['call_put'])
                if len(existing)!=1 or existing[0][:4]!=expected:
                    raise ValueError('Existing canonical contract identity mismatch')
                contract['last_stored_day']=max(contract['last_stored_day'],existing[0][4])
        tasks=plan_tail(contracts,[date.fromisoformat(d) for d in admission['completed_sessions']],
                        attempted_through=date.fromisoformat(admission['attempted_through']),
                        blocked_symbols=admission['blocked_symbols'],receipts=receipts)
        result={'status':'planned','tasks':tasks,'requests':0,'staged_rows':0,
                'admission_sha256':hashlib.sha256(json.dumps(admission,sort_keys=True).encode()).hexdigest()}
        if not execute:
            result['status']='blocked_prior_refusal' if prior_refusal else ('planned' if tasks else 'no_untouched_sessions')
            return result
        stage=home/'staging/option_daily_append.duckdb';stage.parent.mkdir(parents=True,exist_ok=True)
        # A crash after writing the successful receipt but before staging must
        # recover from its raw response; its request remains excluded forever.
        result['recovered_cached_rows']=0
        for receipt in receipts:
            identity=(receipt['params']['source'],receipt['params']['symbol'])
            if receipt['status']=='downloaded' and identity in identities:
                result['recovered_cached_rows']+=stage_receipt(stage,receipt,identities[identity])
        if prior_refusal or not tasks:
            result['status']='blocked_prior_refusal' if prior_refusal else 'no_untouched_sessions'
            if stage.exists():
                result['merge']=merge_stage(home,stage)
            result.pop('tasks',None)
            return result
        result['status']='bounded_attempt_complete'
        for task in tasks[:max_requests]:
            receipt=acquire_task(root,task,fetch);result['requests']+=1
            if receipt['status'] not in ('downloaded','empty'):
                result['status']='stopped_source_response';break
            result['staged_rows']+=stage_receipt(stage,receipt,identities[('alpaca',task['params']['symbol'])])
        if stage.exists():
            result['merge']=merge_stage(home,stage)
        result.pop('tasks',None)
        result['remaining_planned']=len(tasks)-result['requests']
        return result


def main():
    import argparse
    from data_providers.runtime import data_home
    parser=argparse.ArgumentParser(description='Bounded option date append using an audited admission JSON')
    parser.add_argument('--admission',required=True)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--max-requests',type=int,default=10)
    args=parser.parse_args()
    result=run_append(data_home(),json.loads(Path(args.admission).read_text()),execute=args.execute,max_requests=args.max_requests)
    print(json.dumps(result,indent=2))
    # Canonical merging does not claim Parquet publication; existing supervisor
    # publish/verify remains a separate step after this bounded job finishes.


if __name__=='__main__':
    main()
