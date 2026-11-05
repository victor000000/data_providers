"""Host setup must preserve explicit broker endpoints and runtime selection."""
import importlib

from data_providers.providers import tws


def test_native_broker_default_and_override(monkeypatch):
    with monkeypatch.context() as patch:
        patch.delenv('IB_HOST', raising=False)
        patch.setattr(tws.pathlib.Path, 'exists', lambda self: False)
        importlib.reload(tws)
        assert tws.HOST == '127.0.0.1'
        patch.setenv('IB_HOST', 'broker.example')
        importlib.reload(tws)
        assert tws.HOST == 'broker.example'
    importlib.reload(tws)


def test_container_broker_default_retained(monkeypatch):
    with monkeypatch.context() as patch:
        patch.delenv('IB_HOST', raising=False)
        patch.setattr(tws.pathlib.Path, 'exists', lambda self: True)
        importlib.reload(tws)
        assert tws.HOST == 'host.docker.internal'
    importlib.reload(tws)
