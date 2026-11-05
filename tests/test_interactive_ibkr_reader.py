import asyncio
from unittest.mock import AsyncMock, Mock, patch
import pytest
from data_providers.providers.interactive_ibkr import ReadOnlyIBKR


def test_reader_has_no_execution_dispatch():
    reader=ReadOnlyIBKR()
    with pytest.raises(ValueError):reader.market('submit')
    assert reader.loop is None
    assert not hasattr(reader,'submit')


def test_readonly_connection_and_no_startup_order_fetch():
    async def run():
        reader=ReadOnlyIBKR();reader.lock=asyncio.Lock();reader.ib=Mock()
        reader.ib.isConnected.return_value=False;reader.ib.managedAccounts.return_value=['U123']
        reader.ib.connectAsync=AsyncMock()
        with patch('data_providers.providers.interactive_ibkr.ibkr_option_data.expiries',new=AsyncMock(return_value={'expiries':[]})):
            await reader._request('expiries',('SPY',))
        assert reader.ib.connectAsync.await_args.kwargs['readonly'] is True
        assert reader.ib.connectAsync.await_args.kwargs['fetchFields'].value == 0
        assert reader.status()['role']=='market data only'
    asyncio.run(run())
