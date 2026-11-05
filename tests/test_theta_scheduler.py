import fcntl
import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from data_providers.providers.theta_scheduler import request_slot


def test_four_slot_opt_in_bounds_concurrency_and_keeps_legacy_exclusion(tmp_path, monkeypatch):
    monkeypatch.setenv('THETA_HISTORY_SLOTS', '4')
    path = tmp_path / 'history.lock'
    guard = threading.Lock()
    release = threading.Event()
    saturated = threading.Event()
    active = peak = 0

    def run(n):
        nonlocal active, peak
        with request_slot(path, str(n), True):
            with guard:
                active += 1
                peak = max(peak, active)
                if active == 4:
                    saturated.set()
            assert release.wait(5)
            with guard:
                active -= 1

    with ThreadPoolExecutor(max_workers=7) as pool:
        jobs = [pool.submit(run, n) for n in range(7)]
        try:
            assert saturated.wait(3)
            with path.open('a') as exclusive:
                import pytest
                with pytest.raises(BlockingIOError):
                    fcntl.flock(exclusive, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            release.set()
        for job in jobs:
            job.result()
    assert peak == 4


def test_slot_capacity_rejects_unverified_ceiling(monkeypatch):
    from data_providers.providers.theta_scheduler import slot_capacity
    import pytest
    for value in ('0', '3', '5', 'unlimited'):
        monkeypatch.setenv('THETA_HISTORY_SLOTS', value)
        with pytest.raises(ValueError):
            slot_capacity()


def test_two_slots_and_legacy_exclusion(tmp_path):
    path = tmp_path / 'history.lock'
    lock = threading.Lock()
    active = peak = 0
    entered = threading.Event()
    release = threading.Event()
    def run(n):
        nonlocal active, peak
        with request_slot(path, str(n), True):
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    entered.set()
            assert release.wait(3)
            with lock:
                active -= 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(run, n) for n in range(4)]
        assert entered.wait(3)
        with path.open('a') as legacy:
            try:
                fcntl.flock(legacy, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise AssertionError('legacy exclusive lock overlapped requests')
        release.set()
        for f in futures:
            f.result()
    assert peak == 2
    with request_slot(path, 'legacy', False):
        with path.open('a') as check:
            import pytest
            with pytest.raises(BlockingIOError):
                fcntl.flock(check, fcntl.LOCK_SH | fcntl.LOCK_NB)


@pytest.mark.parametrize("capacity", ["2", "4"])
def test_parallel_same_key_only_requests_once(tmp_path, monkeypatch, capacity):
    monkeypatch.setenv("THETA_HISTORY_SLOTS", capacity)
    from data_providers.providers import theta_history as mod
    monkeypatch.setattr(mod, 'data_home', lambda: tmp_path)
    monkeypatch.setattr(mod, 'pace_parallel', lambda _: None)
    clients = [mod.HistoryClient(tmp_path/'receipts', parallel=True) for _ in range(4)]
    calls = []
    class Response:
        status_code = 200
        content = b'symbol,timestamp\nSPY,2024-04-12T12:00:00\n'
    def get(*a, **k):
        calls.append(1)
        time.sleep(.05)
        return Response()
    for c in clients:
        monkeypatch.setattr(c.client, '_rate_limit', lambda: None)
        monkeypatch.setattr(c.client.session, 'get', get)
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(lambda c: c.request('option/history/quote', {'symbol': 'SPY'}), clients))
    assert len(calls) == 1
    assert all(r[0]["status"] in {"downloaded", "in_flight"} for r in result)
    assert all(c.request("option/history/quote", {"symbol": "SPY"})[0]["status"] == "downloaded" for c in clients)


@pytest.mark.parametrize("capacity", ["2", "4"])
def test_parallel_global_claim_reuses_other_folder_and_fails_closed(tmp_path, monkeypatch, capacity):
    monkeypatch.setenv("THETA_HISTORY_SLOTS", capacity)
    import pytest
    from data_providers.providers import theta_history as mod
    monkeypatch.setattr(mod, 'data_home', lambda: tmp_path)
    monkeypatch.setattr(mod, 'pace_parallel', lambda _: None)
    first = mod.HistoryClient(tmp_path/'first', parallel=True)
    second = mod.HistoryClient(tmp_path/'second', parallel=True)
    class Response:
        status_code = 403
        content = b'not entitled'
    monkeypatch.setattr(first.client, '_rate_limit', lambda: None)
    monkeypatch.setattr(first.client.session, 'get', lambda *a, **k: Response())
    monkeypatch.setattr(second.client.session, 'get', lambda *a, **k: pytest.fail('replayed claim'))
    receipt = first.request('option/history/quote', {'symbol':'SPY'})
    assert second.request('option/history/quote', {'symbol':'SPY'}) == receipt
    next(first.folder.glob('*.json')).unlink()
    with pytest.raises(FileNotFoundError):
        second.request('option/history/quote', {'symbol':'SPY'})


@pytest.mark.parametrize('capacity', [2, 4])
def test_request_many_uses_independent_sessions_and_preserves_order(tmp_path, monkeypatch, capacity):
    from data_providers.providers import theta_history as mod
    monkeypatch.setenv('THETA_HISTORY_SLOTS', str(capacity))
    monkeypatch.setattr(mod, 'data_home', lambda: tmp_path)
    monkeypatch.setattr(mod, 'pace_parallel', lambda _: None)
    mutex = threading.Lock()
    active = peak = closed = 0
    rendezvous = threading.Barrier(capacity)
    owners = {}
    class Client:
        def __init__(self, base_url=None):
            self.base_url = 'http://localhost/v3'
            self.session = self
        def _rate_limit(self):
            pass
        def close(self):
            nonlocal closed
            closed += 1
        def get(self, url, *, params, timeout):
            nonlocal active, peak
            with mutex:
                owners.setdefault(id(self), set()).add(threading.get_ident())
                active += 1
                peak = max(peak, active)
            rendezvous.wait(timeout=3)
            time.sleep(.02)
            with mutex:
                active -= 1
            class Response:
                status_code = 200
                content = f'symbol,timestamp\n{params["symbol"]},2024-04-12T12:00:00\n'.encode()
            return Response()
    monkeypatch.setattr(mod, 'ThetaDataClient', Client)
    client = mod.HistoryClient(tmp_path/'batch', parallel=True)
    result = list(client.request_many([('option/history/quote', {'symbol': str(n)}) for n in range(capacity * 2)]))
    assert [rows[0]['symbol'] for receipt, rows in result] == [str(n) for n in range(capacity * 2)]
    assert peak == capacity and closed == capacity
    assert len(owners) == capacity and all(len(threads) == 1 for threads in owners.values())


def test_completion_mode_progresses_past_slow_first_request_and_bounds_input(tmp_path, monkeypatch):
    from data_providers.providers import theta_history as mod
    monkeypatch.setenv('THETA_HISTORY_SLOTS', '4')
    monkeypatch.setattr(mod, 'data_home', lambda: tmp_path)
    monkeypatch.setattr(mod, 'pace_parallel', lambda _: None)
    release = threading.Event()
    slow_started = threading.Event()
    consumed = []
    def requests():
        for n in range(20):
            consumed.append(n)
            yield 'option/history/quote', {'symbol': str(n)}
    def request(self, endpoint, params):
        if params['symbol'] == '0':
            slow_started.set()
            assert release.wait(5)
        return {'params': params}, [params['symbol']]
    monkeypatch.setattr(mod.HistoryClient, 'request', request)
    client = mod.HistoryClient(tmp_path/'batch', parallel=True)
    results = client.request_many(requests(), ordered=False)
    try:
        first = next(results)
        assert slow_started.wait(3)
        assert first[1] != ['0']
        assert len(consumed) == 4
        # Replacement work keeps flowing while the first request is blocked.
        others = [next(results) for _ in range(7)]
        assert all(rows != ['0'] for _, rows in others)
        assert len(consumed) == 11
    finally:
        release.set()
    remainder = list(results)
    assert sorted(int(rows[0]) for _, rows in [first, *others, *remainder]) == list(range(20))


def test_completion_mode_close_retains_started_requests_and_closes_sessions(tmp_path, monkeypatch):
    from data_providers.providers import theta_history as mod
    monkeypatch.setenv('THETA_HISTORY_SLOTS', '2')
    monkeypatch.setattr(mod, 'data_home', lambda: tmp_path)
    monkeypatch.setattr(mod, 'pace_parallel', lambda _: None)
    rendezvous = threading.Barrier(2)
    calls, closed = [], []
    class Client:
        def __init__(self, base_url=None):
            self.base_url = 'http://localhost/v3'
            self.session = self
        def _rate_limit(self):
            pass
        def close(self):
            closed.append(self)
        def get(self, url, *, params, timeout):
            calls.append(params['symbol'])
            rendezvous.wait(timeout=3)
            class Response:
                status_code = 200
                content = b'symbol,timestamp\nSPY,2024-04-12T12:00:00\n'
            return Response()
    monkeypatch.setattr(mod, 'ThetaDataClient', Client)
    client = mod.HistoryClient(tmp_path/'batch', parallel=True)
    results = client.request_many((('option/history/quote', {'symbol': str(n)}) for n in range(10)), ordered=False)
    next(results)
    results.close()
    assert sorted(calls) == ['0', '1']
    assert len(closed) == 2
    import json
    assert all(json.loads(p.read_text())['status'] == 'downloaded' for p in client.folder.glob('*.json'))


def test_inflight_receipt_is_not_visible_until_fsynced(tmp_path, monkeypatch):
    import json
    from data_providers.providers import theta_history as mod
    monkeypatch.setattr(mod, 'data_home', lambda: tmp_path)
    client = mod.HistoryClient(tmp_path/'atomic')
    monkeypatch.setattr(client.client, '_rate_limit', lambda: None)
    original = mod.os.fsync
    seen = []
    def fsync(fd):
        if not seen:
            assert not list(client.folder.glob('*.json'))
        seen.append(fd)
        return original(fd)
    monkeypatch.setattr(mod.os, 'fsync', fsync)
    def get(*a, **k):
        receipt = json.loads(next(client.folder.glob('*.json')).read_text())
        assert receipt['status'] == 'in_flight'
        raise TimeoutError('retained failure')
    monkeypatch.setattr(client.client.session, 'get', get)
    receipt, rows = client.request('index/history/eod', {'symbol':'VIX9D'})
    assert receipt['status'] == 'request_failed' and not rows
    assert not list(client.folder.glob('*.pending'))


def test_waiting_exclusive_request_blocks_new_shared_admission(tmp_path):
    """Continuous parallel requests must not overtake an admitted writer."""
    path = tmp_path / 'history.lock'
    order = []
    release_first = threading.Event()
    first_entered = threading.Event()
    def first():
        with request_slot(path, 'first', True):
            first_entered.set()
            assert release_first.wait(5)
    def later(key, parallel):
        with request_slot(path, key, parallel):
            order.append(key)
    with ThreadPoolExecutor(max_workers=3) as pool:
        a = pool.submit(first)
        assert first_entered.wait(3)
        b = pool.submit(later, 'exclusive', False)
        try:
            deadline = time.monotonic() + 3
            while True:
                with (tmp_path/'theta-admission.lock').open('a') as check:
                    try:
                        fcntl.flock(check, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        break
                assert time.monotonic() < deadline
                time.sleep(.01)
            c = pool.submit(later, 'new_shared', True)
        finally:
            release_first.set()
        a.result(); b.result(); c.result()
    assert order == ['exclusive', 'new_shared']


def test_saturated_slots_bound_shared_gate_waiters(tmp_path, monkeypatch):
    """Queued work must not pin arbitrarily many shared gates behind two slots."""
    path = tmp_path / 'history.lock'
    path.touch()
    gate_inode = path.stat().st_ino
    original = fcntl.flock
    guard = threading.Lock()
    release = threading.Event()
    two_active = threading.Event()
    slot_waiter = threading.Event()
    shared_calls = active = 0

    def observed(file, operation):
        nonlocal shared_calls
        import os
        fd = file if isinstance(file, int) else file.fileno()
        if os.fstat(fd).st_ino == gate_inode and operation == fcntl.LOCK_SH:
            with guard:
                shared_calls += 1
                if shared_calls == 3:
                    slot_waiter.set()
        return original(file, operation)

    monkeypatch.setattr(fcntl, 'flock', observed)

    def request(key):
        nonlocal active
        with request_slot(path, key, True):
            with guard:
                active += 1
                if active == 2:
                    two_active.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = [pool.submit(request, str(i)) for i in range(2)]
        try:
            assert two_active.wait(3)
            jobs.append(pool.submit(request, 'third'))
            assert slot_waiter.wait(3)
            # The slot waiter must still own admission. With the old order this
            # lock was free and all subsequent callers could pin shared gates.
            with (tmp_path/'theta-admission.lock').open('a') as admission:
                import pytest
                with pytest.raises(BlockingIOError):
                    original(admission, fcntl.LOCK_EX | fcntl.LOCK_NB)
            jobs.extend(pool.submit(request, str(i)) for i in range(3, 6))
            with guard:
                assert shared_calls == 3
        finally:
            release.set()
        for job in jobs:
            job.result()


@pytest.mark.parametrize("capacity,expected_wait", [("2", 0.4), ("4", 0.0)])
def test_shared_start_pacing_respects_legacy_and_paid_concurrency(tmp_path, monkeypatch, capacity, expected_wait):
    from data_providers.providers import theta_scheduler as mod
    monkeypatch.setenv('THETA_HISTORY_SLOTS', capacity)
    (tmp_path/'theta-parallel-pacing.lock').write_text('99.9')
    monkeypatch.setattr(mod.time, 'monotonic', lambda: 100.0)
    waits = []
    monkeypatch.setattr(mod.time, 'sleep', waits.append)
    mod.pace_parallel(tmp_path/'history.lock')
    assert sum(waits) == pytest.approx(expected_wait)
    assert float((tmp_path/'theta-parallel-pacing.lock').read_text()) == 100.0


@pytest.mark.parametrize("capacity,interval", [("2", 0.5), ("4", 0.0)])
def test_paid_parallel_client_does_not_add_another_start_delay(tmp_path, monkeypatch, capacity, interval):
    from data_providers.providers.theta_history import HistoryClient
    monkeypatch.setenv('THETA_HISTORY_SLOTS', capacity)
    client = HistoryClient(tmp_path/'receipts', base_url='http://127.0.0.1:25503/v3', parallel=True)
    assert client.client._min_interval == interval
    client.client.session.close()

@pytest.mark.parametrize('parallel', [False, True])
def test_admission_freeze_after_slot_wait_leaves_key_untouched(tmp_path, monkeypatch, parallel):
    from contextlib import contextmanager
    from data_providers.providers import theta_history as mod
    monkeypatch.setattr(mod, 'data_home', lambda: tmp_path)
    waiting, release, stopped = threading.Event(), threading.Event(), threading.Event()
    @contextmanager
    def queued_slot(*args):
        waiting.set()
        assert release.wait(3)
        yield
    monkeypatch.setattr(mod, 'request_slot', queued_slot)
    client = mod.HistoryClient(tmp_path/'receipts', parallel=parallel)
    def admit():
        if stopped.is_set():
            raise mod.HistoryAdmissionStopped()
    monkeypatch.setattr(client.client.session, 'get', lambda *a, **k: pytest.fail('HTTP after freeze'))
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(client.request, 'option/history/quote', {'symbol':'SPY'}, admit=admit)
        assert waiting.wait(3)
        stopped.set(); release.set()
        with pytest.raises(mod.HistoryAdmissionStopped):
            job.result()
    assert not list((tmp_path/'receipts').glob('*.json'))
    assert not list((tmp_path/'var/locks/theta-request-claims').glob('*.json'))


def test_frozen_admission_still_allows_retained_receipt_read(tmp_path, monkeypatch):
    from data_providers.providers import theta_history as mod
    monkeypatch.setattr(mod, 'data_home', lambda: tmp_path)
    monkeypatch.setattr(mod, 'pace_parallel', lambda _: None)
    client = mod.HistoryClient(tmp_path/'receipts', parallel=True)
    monkeypatch.setattr(client.client, '_rate_limit', lambda: None)
    class Response:
        status_code = 200
        content = b'symbol,timestamp\nSPY,2024-04-12T12:00:00\n'
    monkeypatch.setattr(client.client.session, 'get', lambda *a, **k: Response())
    first = client.request('option/history/quote', {'symbol':'SPY'})
    def deny():
        raise mod.HistoryAdmissionStopped()
    monkeypatch.setattr(client.client.session, 'get', lambda *a, **k: pytest.fail('Cache replay'))
    assert client.request('option/history/quote', {'symbol':'SPY'}, admit=deny) == first
