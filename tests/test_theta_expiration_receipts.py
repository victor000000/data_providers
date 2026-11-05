"""Expiry metadata must retain the same immutable receipt/claim safeguards."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_providers.providers import theta_history


@pytest.mark.parametrize('status,body,expected', [
    (200, b'symbol,expiration\nSPY,2026-10-16\nSPY,2026-11-20\n', 'downloaded'),
    (472, b'No data found for your request', 'missing_upstream'),
    (503, b'Unavailable', 'request_failed'),
])
def test_expiration_receipts_survive_cross_folder_retry(tmp_path, monkeypatch, status, body, expected):
    monkeypatch.setattr(theta_history, 'data_home', lambda: tmp_path)
    monkeypatch.setattr(theta_history, 'pace_parallel', lambda _: None)
    calls = []
    clients = [theta_history.HistoryClient(tmp_path / name, parallel=True)
               for name in ['first', 'second']]

    def fetch(*args, **kwargs):
        calls.append(kwargs['params'])
        return SimpleNamespace(status_code=status, content=body)

    try:
        for client in clients:
            monkeypatch.setattr(client.client.session, 'get', fetch)
            monkeypatch.setattr(client.client, '_rate_limit', lambda: None)
        receipt, rows = clients[0].request('option/list/expirations', {'symbol': 'SPY'})
        replay, replay_rows = clients[1].request('option/list/expirations', {'symbol': 'SPY'}, retry=True)
        assert len(calls) == 1
        assert receipt == replay and rows == replay_rows
        assert receipt['status'] == expected
        assert Path(receipt['raw_path']).read_bytes() == body
        assert receipt['body_sha256'] == hashlib.sha256(body).hexdigest()
        assert rows == ([{'symbol': 'SPY', 'expiration': '2026-10-16'},
                         {'symbol': 'SPY', 'expiration': '2026-11-20'}] if status == 200 else [])
    finally:
        for client in clients:
            client.client.session.close()
