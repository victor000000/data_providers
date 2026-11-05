import hashlib
import json
from unittest.mock import Mock

import pytest
import requests

from data_providers.providers import massive_futures


def test_resume_reuses_successful_empty_and_nonempty_raw_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(massive_futures,'load_api_key',lambda:'test-key')
    path='/futures/v1/aggs/ESZ6'
    params={'resolution':'1session','window_start.lte':'2026-10-16'}
    request=requests.Request('GET','https://api.massive.com'+path,params=params).prepare()
    key=hashlib.sha256(request.path_url.encode()).hexdigest()
    for rows in ([],[{'close':123}]):
        for p in tmp_path.glob('*.json'):p.unlink()
        body=json.dumps({'status':'OK','results':rows}).encode()
        (tmp_path/(key+'-'+hashlib.sha256(body).hexdigest()+'.json')).write_bytes(body)
        client=massive_futures.FuturesREST(tmp_path,reuse_success=True)
        client.session.get=Mock(side_effect=AssertionError('Duplicate network download'))
        assert list(client.pages(path,params))==rows
        assert client.responses_reused==1
        client.session.get.assert_not_called()


def test_corrupt_cached_bytes_fail_without_requesting_again(tmp_path, monkeypatch):
    monkeypatch.setattr(massive_futures,'load_api_key',lambda:'test-key')
    path='/futures/v1/aggs/ESZ6'
    key=hashlib.sha256(path.encode()).hexdigest()
    (tmp_path/(key+'-'+'0'*64+'.json')).write_text('{"status":"OK"}')
    client=massive_futures.FuturesREST(tmp_path,reuse_success=True)
    client.session.get=Mock(side_effect=AssertionError('Duplicate download'))
    with pytest.raises(ValueError,match='checksum'):
        list(client.pages(path,{}))
    client.session.get.assert_not_called()
