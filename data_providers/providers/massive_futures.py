"""Authenticated futures REST transport. Credentials never appear in URLs or receipts."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import requests

from data_providers.providers.massive import load_api_key
from data_providers.shared.net import throttle


class FuturesREST:
    def __init__(self, raw_dir, *, reuse_success=False):
        self.raw_dir = Path(raw_dir)
        self.raw_dir.mkdir(parents=True,exist_ok=True)
        self.reuse_success = reuse_success
        self.responses_reused = 0
        self.session = requests.Session()
        key = load_api_key()
        if not key:
            raise RuntimeError('Massive credential is missing')
        self.session.headers['Authorization'] = 'Bearer ' + key

    def pages_one(self, path, params):
        yield from self.pages(path, params, single=True)

    def pages(self, path, params, *, single=False):
        url = 'https://api.massive.com' + path
        seen = set()
        while url:
            if url in seen or urlparse(url).netloc != 'api.massive.com' or urlparse(url).scheme != 'https':
                raise ValueError('Invalid futures pagination')
            seen.add(url)
            data = None
            if self.reuse_success:
                path_url = requests.Request('GET',url,params=params).prepare().path_url
                key = hashlib.sha256(path_url.encode()).hexdigest()
                for cached in sorted(self.raw_dir.glob(key+'-*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
                    body = cached.read_bytes()
                    if hashlib.sha256(body).hexdigest() != cached.stem.split('-')[-1]:
                        raise ValueError('Cached futures response checksum mismatch')
                    payload = json.loads(body)
                    if payload.get('status') in ('OK','DELAYED'):
                        data = payload
                        self.responses_reused += 1
                        break
            if data is None:
                throttle('massive')
                response = self.session.get(url,params=params,timeout=(10,45))
                # Record server bytes and safe request inputs, including failures.
                filename = hashlib.sha256(response.request.path_url.encode()).hexdigest()+'-'+hashlib.sha256(response.content).hexdigest()
                (self.raw_dir/(filename+'.json')).write_bytes(response.content)
                if response.status_code != 200:
                    raise RuntimeError(f'Massive HTTP {response.status_code}')
                data = response.json()
            if data.get('status') not in ('OK','DELAYED'):
                raise RuntimeError('Massive did not return successful futures data')
            self.next_url = data.get('next_url')
            yield from data.get('results',[])
            if single:
                break
            url,params = data.get('next_url'),None
