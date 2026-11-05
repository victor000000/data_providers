"""Credentials are loaded at request time, never while importing a job."""
import json
import os
from collections.abc import Mapping

from .runtime import data_home


def alpaca_headers():
    key = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        path = data_home() / "secrets" / "alpaca.json"
        if path.exists():
            settings = json.loads(path.read_text())
            key = key or settings.get("key_id")
            secret = secret or settings.get("secret")
    if not key or not secret:
        raise RuntimeError("Alpaca credentials are missing; configure environment or secrets/alpaca.json")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


class AlpacaHeaders(Mapping):
    """Mapping preserves the existing HDR interface for requests and urllib."""
    def __getitem__(self, key):
        return alpaca_headers()[key]

    def __iter__(self):
        return iter(alpaca_headers())

    def __len__(self):
        return 2
