"""Strict, receipt-backed bounded Theta v3 history. Empty is never a transport status."""
import csv
import hashlib
import io
import json
import math
import os
import time
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from data_providers.providers.thetadata import ThetaDataClient
from data_providers.runtime import data_home
from data_providers.providers.theta_scheduler import request_slot, pace_parallel, slot_capacity

NY = ZoneInfo('America/New_York')


class HistoryAdmissionStopped(RuntimeError):
    """A queued request was declined before reserving a key or sending HTTP."""

ENDPOINTS = {'option/history/quote','option/list/contracts/quote','option/list/expirations','option/history/open_interest',
             'option/history/greeks/eod','stock/history/ohlc','stock/history/eod',
             'index/history/price','index/history/eod','option/list/dates/quote','stock/list/dates/quote','index/list/dates/price'}


def utc(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        # US regular session timestamps are unambiguous even on DST change weeks.
        parsed = parsed.replace(tzinfo=NY)
    return parsed.astimezone(UTC)


def number(value):
    if value in (None, ''):
        return None
    n = float(value)
    if not math.isfinite(n):
        raise ValueError('Nonfinite provider observation')
    return n


def validate_ohlcv(row):
    values = [number(row.get(k)) for k in ('open','high','low','close')]
    if any(v is None or v <= 0 for v in values):
        raise ValueError('Missing or nonpositive underlying price')
    op, high, low, close = values
    if high < max(op, low, close) or low > min(op, high, close):
        raise ValueError('Inconsistent underlying OHLC')
    volume = number(row.get('volume'))
    if volume is None or volume < 0 or volume != int(volume):
        raise ValueError('Invalid underlying volume')
    return values + [volume]


def contract_id(symbol, expiry, right, strike):
    side = {'C':'C','P':'P','call':'C','put':'P'}.get(right)
    if not side or Decimal(str(strike)) <= 0:
        raise ValueError('Invalid option terms')
    return f"US:OPRA:{symbol}:{expiry}:{side}:{Decimal(str(strike)).normalize():f}"


def quote(row, *, cid, symbol, session, receipt):
    stamp = utc(row['timestamp'])
    if stamp.astimezone(NY).date().isoformat() != session:
        raise ValueError('Quote outside requested session')
    bid, ask = number(row.get('bid')), number(row.get('ask'))
    if any(x is not None and x < 0 for x in (bid,ask)):
        raise ValueError('Negative quote price')
    bs, ass = number(row.get('bid_size')), number(row.get('ask_size'))
    if any(x is not None and (x < 0 or x != int(x)) for x in (bs,ass)):
        raise ValueError('Invalid quote size')
    quality = ('one_sided' if bid in (None,0) or ask in (None,0) or bs in (None,0) or ass in (None,0)
               else 'crossed' if bid > ask else 'locked' if bid == ask else 'two_sided_age_unknown')
    # The interval endpoint exposes a sampled NBBO, not the originating update time.
    # Never use its interval timestamp to claim a zero-age quote.
    return ('thetadata_v3',cid,symbol,session,stamp,None,None,bid,ask,bs,ass,
            str(row.get('bid_exchange','')),str(row.get('ask_exchange','')),
            str(row.get('bid_condition','')),str(row.get('ask_condition','')),
            None,'last_nbbo_at_interval','America/New_York',quality,receipt)


class HistoryClient:
    def __init__(self, folder, base_url=None, *, read_timeout=30, parallel=False):
        if isinstance(read_timeout, bool) or not isinstance(read_timeout, (int, float)) or not math.isfinite(read_timeout) or not 1 <= read_timeout <= 180:
            raise ValueError('read_timeout must be finite and between 1 and 180 seconds')
        if not isinstance(parallel, bool):
            raise ValueError("parallel must be a boolean")
        self.parallel = parallel
        self.read_timeout = read_timeout
        self.folder = Path(folder)
        (self.folder/'raw').mkdir(parents=True,exist_ok=True)
        self.client = ThetaDataClient(base_url=base_url)
        if parallel and slot_capacity() == 4:
            # Explicit Standard-tier opt-in retains shared four-slot admission.
            # v3 paid requests have no separate requests-per-second ceiling.
            self.client._min_interval = 0.0

    def request(self, endpoint, params, *, retry=False, admit=None):
        if endpoint not in ENDPOINTS:
            raise ValueError('Unsupported research endpoint')
        params = dict(params,format='csv')
        key = hashlib.sha256(json.dumps([endpoint,params],sort_keys=True).encode()).hexdigest()
        saved = self.folder/(key+'.json')
        if saved.exists():
            # Every retained key blocks replay, including failed/uncertain attempts.
            # Keep the retry keyword compatible, but it cannot bypass this ledger.
            receipt = json.loads(saved.read_text())
            return receipt, self._rows(receipt)
        lockfile = Path(data_home())/'var/locks/thetadata-history.lock'
        lockfile.parent.mkdir(parents=True,exist_ok=True)
        now = datetime.now(UTC).isoformat()
        receipt = dict(endpoint=endpoint,params=params,retrieved_at=now,source='thetadata_v3',
                       status='request_failed',http_status=None,body_sha256=None,rows=0)
        receipt.update(datasource_tag='thetadata_v3/' + endpoint, provider='thetadata',
                       feed='v3', method_parameters=dict(params))
        if 'greeks' in endpoint:
            receipt.update(consistency_tag='cross_source_inconsistent',
                           comparison_status='not_compared')
        receipt['timeout_policy'] = dict(connect_seconds=5, read_idle_seconds=self.read_timeout,
                                        total_deadline=False, automatic_retries=0)
        parsed = []
        queued = time.monotonic()
        with request_slot(lockfile, key, self.parallel):
            acquired = time.monotonic()
            # A competing caller may have completed while this caller waited.
            if saved.exists():
                retained = json.loads(saved.read_text())
                return retained, self._rows(retained)
            if self.parallel:
                # A global exact-key claim also protects opt-in callers using
                # different receipt folders. Missing claim targets fail closed:
                # they may represent an interrupted reservation, never a retry.
                claims = lockfile.parent / 'theta-request-claims'
                claims.mkdir(exist_ok=True)
                claim = claims / (key + '.json')
                if claim.exists():
                    target = Path(json.loads(claim.read_text())['receipt_path'])
                    retained = json.loads(target.read_text())
                    return retained, self._rows(retained)
                if admit is not None:
                    admit()
                with claim.open('x') as reservation:
                    json.dump({'receipt_path': str(saved.resolve())}, reservation)
                    reservation.flush()
                    os.fsync(reservation.fileno())
                pace_parallel(lockfile)
            elif admit is not None:
                admit()
            self.client._rate_limit()
            receipt['timing'] = dict(lock_wait_seconds=acquired-queued,
                                    pacing_seconds=time.monotonic()-acquired,
                                    measurement='monotonic local elapsed; decoded response bytes are not wire bytes')
            receipt['status'] = 'in_flight'
            # Publish the fsynced in-flight marker atomically. A concurrent
            # cache reader must never observe an empty/partially written JSON.
            # link() retains exclusive-create semantics rather than replacing
            # another caller's receipt.
            with tempfile.NamedTemporaryFile(mode='w', dir=self.folder,
                                             prefix=key+'.', suffix='.pending',
                                             delete=False) as checkpoint:
                pending = Path(checkpoint.name)
                try:
                    json.dump(receipt, checkpoint, indent=2)
                    checkpoint.flush()
                    os.fsync(checkpoint.fileno())
                    os.link(pending, saved)
                finally:
                    pending.unlink(missing_ok=True)
            receipt['status'] = 'request_failed'
            http_start = time.monotonic()
            receipt['http_started_utc'] = datetime.now(UTC).isoformat()
            try:
                r=self.client.session.get(self.client.base_url+'/'+endpoint,params=params,timeout=(5,self.read_timeout))
                http_end = time.monotonic()
                receipt['http_completed_utc'] = datetime.now(UTC).isoformat()
                receipt['timing']['http_seconds'] = http_end-http_start
                elapsed = getattr(r, 'elapsed', None)
                if elapsed is not None:
                    headers_seconds = elapsed.total_seconds()
                    receipt['timing']['response_headers_seconds'] = headers_seconds
                    receipt['timing']['post_headers_seconds_estimate'] = max(0.0, http_end-http_start-headers_seconds)
                    receipt['timing']['headers_basis'] = 'Requests elapsed to headers; includes server processing, not isolated network latency'
                receipt['http_status']=r.status_code
                body=r.content
                receipt['timing']['decoded_response_bytes'] = len(body)
                sha=hashlib.sha256(body).hexdigest()
                raw=self.folder/'raw'/(key+'-'+sha+'.csv')
                if not raw.exists(): raw.write_bytes(body)
                receipt.update(body_sha256=sha,raw_path=str(raw))
                # Decode once using the same UTF-8/newline policy as retained raw
                # receipts. Requests.text can repeatedly sniff/decode multi-MB CSV.
                text = io.TextIOWrapper(io.BytesIO(body), encoding='utf-8').read()
                lower=text.lower()
                if r.status_code in (401,403) or 'subscription' in lower or 'not entitled' in lower:
                    receipt['status']='not_entitled'
                elif r.status_code == 472:
                    receipt['status']='missing_upstream'
                elif r.status_code == 200:
                    parsed=list(csv.DictReader(io.StringIO(text)))
                    if not parsed or not parsed[0] or not ({'timestamp','created','date','symbol'} & set(parsed[0])):
                        receipt['status']='missing_upstream' if not text.strip() else 'invalid_response'
                    else:
                        # Verify the persisted bytes, including any existing raw
                        # file, before returning the already parsed rows.
                        if hashlib.sha256(raw.read_bytes()).hexdigest() != sha:
                            raise ValueError('Raw receipt checksum mismatch')
                        receipt.update(status='downloaded',rows=len(parsed))
            except Exception as exc:
                receipt['error']=type(exc).__name__
                receipt['timing']['attempt_elapsed_seconds'] = time.monotonic()-http_start
            temp=saved.with_suffix('.tmp')
            with temp.open('w') as checkpoint:
                json.dump(receipt, checkpoint, indent=2)
                checkpoint.flush()
                os.fsync(checkpoint.fileno())
            temp.replace(saved)
        # The fresh response was parsed and verified above; do not read/decode/
        # parse it a second time after releasing the shared request lock.
        return receipt, parsed if receipt['status'] == 'downloaded' else []

    def request_many(self, requests, *, ordered=True):
        """Run an audited allocation within the configured shared slot ceiling.

        Each worker owns a reusable HTTP session. By default results retain input
        order. ``ordered=False`` yields completed responses without waiting for a
        slow earlier request, keeping at most one outstanding result per worker.
        Existing receipts (including failures) are reused. Callers still own
        cross-folder historical/semantic overlap and entitlement checks, and must
        use receipt parameters rather than input position in completion mode.
        """
        if not self.parallel:
            raise ValueError('request_many requires explicit parallel=True')
        if not isinstance(ordered, bool):
            raise ValueError('ordered must be a boolean')
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
        import threading
        workers = slot_capacity()
        local = threading.local()
        clients = []
        clients_lock = threading.Lock()
        def run(item):
            endpoint, params = item
            if not hasattr(local, 'client'):
                local.client = HistoryClient(self.folder, self.client.base_url,
                                             read_timeout=self.read_timeout, parallel=True)
                with clients_lock:
                    clients.append(local.client)
            return local.client.request(endpoint, params)
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                if ordered:
                    yield from pool.map(run, requests, buffersize=workers)
                else:
                    items = iter(requests)
                    pending = set()
                    try:
                        for _ in range(workers):
                            item = next(items, None)
                            if item is None:
                                break
                            pending.add(pool.submit(run, item))
                        while pending:
                            done, _ = wait(pending, return_when=FIRST_COMPLETED)
                            while done:
                                future = done.pop()
                                pending.remove(future)
                                yield future.result()
                                # Do not retain already consumed large responses
                                # while replacement downloads fill the pool.
                                del future
                                item = next(items, None)
                                if item is not None:
                                    pending.add(pool.submit(run, item))
                    finally:
                        # On caller stop, cancel only work that has not started.
                        # Active requests finish and keep their durable receipts.
                        for future in pending:
                            future.cancel()
        finally:
            # The executor has joined workers before closing their connections,
            # including on an exception or explicit generator close.
            for client in clients:
                client.client.session.close()

    @staticmethod
    def _rows(receipt):
        if receipt['status']!='downloaded': return []
        path=Path(receipt['raw_path'])
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest()!=receipt['body_sha256']:
            raise ValueError('Raw receipt checksum mismatch')
        # Hash and parse the same bytes, eliminating a second disk read and a
        # possible file change between checksum verification and parsing.
        return list(csv.DictReader(io.TextIOWrapper(io.BytesIO(body), encoding='utf-8')))
