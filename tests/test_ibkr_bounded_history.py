import asyncio
from types import SimpleNamespace

import pytest

from data_providers.providers.ibkr_bounded_history import request_history


def test_timeout_does_not_cancel_a_different_concurrent_request():
    async def run():
        pending, cancelled = {}, []

        def start(key, contract, container):
            future = asyncio.get_running_loop().create_future()
            pending[key] = future
            if key == 2:
                asyncio.get_running_loop().call_later(.001, future.set_result, [])
            return future

        def end(key):
            pending.pop(key, None)

        ib = SimpleNamespace(
            wrapper=SimpleNamespace(startReq=start, _endReq=end),
            client=SimpleNamespace(reqHistoricalData=lambda *a: None,
                                   cancelHistoricalData=cancelled.append))

        async def one(key):
            return await request_history(
                ib, None, request_id=key, end='20261030 00:00:00 UTC',
                duration='1 D', bar_size='1 day',
                what_to_show='OPTION_IMPLIED_VOLATILITY', timeout=.02)

        result = await asyncio.gather(one(1), one(2), return_exceptions=True)
        assert isinstance(result[0], asyncio.TimeoutError)
        assert isinstance(result[1], list)
        assert cancelled == [1]
        assert not pending

    asyncio.run(run())
