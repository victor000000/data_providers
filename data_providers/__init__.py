"""Shared data infrastructure. Import MarketData for read-only consumer access."""

__version__ = "0.1.0"


def __getattr__(name):
    if name == "MarketData":
        from .client import MarketData
        return MarketData
    raise AttributeError(name)
