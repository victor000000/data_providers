import unittest
from unittest.mock import AsyncMock,patch
from types import SimpleNamespace
from data_providers.providers import ibkr_option_data


class Event:
    def __iadd__(self,f):return self
    def __isub__(self,f):return self


class InteractiveIBKRTests(unittest.IsolatedAsyncioTestCase):
    async def test_unqualified_parameter_combinations_are_not_chain_contracts(self):
        details={'expiries':['2099-12-18'],'strikes':[545,550]}
        rows=[{'symbol':'a','contract_status':'qualified'},{'symbol':'b','contract_status':'unqualified'}]
        with patch.object(ibkr_option_data,'expiries',new=AsyncMock(return_value=details)),patch.object(ibkr_option_data,'snapshots',new=AsyncMock(return_value={'rows':rows})):
            result=await ibkr_option_data.chain(object(),'SPY','2099-12-18','P',540,560)
        self.assertEqual([r['symbol'] for r in result['rows']],['a'])

    async def test_requests_are_cancelled_even_if_wait_is_cancelled(self):
        class IB:
            def __init__(self):self.errorEvent=Event();self.cancelled=[]
            async def qualifyContractsAsync(self,*contracts):
                c=contracts[0];c.conId=1;c.localSymbol='SPY   991218P00550000';return [c]
            def reqMarketDataType(self,t):pass
            def reqMktData(self,*args):return SimpleNamespace(marketDataType=1,bid=float('nan'),ask=float('nan'))
            def cancelMktData(self,c):self.cancelled.append(c.conId)
        ib=IB()
        with patch('data_providers.providers.ibkr_option_data.asyncio.sleep',new=AsyncMock(side_effect=RuntimeError('interrupted'))):
            with self.assertRaises(RuntimeError):await ibkr_option_data.snapshots(ib,['SPY991218P00550000'])
        self.assertEqual(ib.cancelled,[1])

    async def test_waits_for_first_book_instead_of_cancelling_after_two_seconds(self):
        ticks=SimpleNamespace(bid=float('nan'),ask=float('nan'),bidSize=1,askSize=1,marketDataType=2,time=None)
        class IB:
            def __init__(self):self.errorEvent=Event();self.cancelled=[]
            async def qualifyContractsAsync(self,*contracts):
                c=contracts[0];c.conId=1;c.localSymbol='SPY   991218P00550000';return [c]
            def reqMarketDataType(self,t):pass
            def reqMktData(self,*args):return ticks
            def cancelMktData(self,c):self.cancelled.append(c.conId)
        waits=[]
        async def receive(delay):
            waits.append(delay)
            if len(waits)==6:ticks.bid=1;ticks.ask=1.2;ticks.marketDataType=2
        ib=IB()
        with patch('data_providers.providers.ibkr_option_data.asyncio.sleep',side_effect=receive):
            result=await ibkr_option_data.snapshots(ib,['SPY991218P00550000'],2)
        self.assertEqual(len(waits),6)
        self.assertEqual(result['rows'][0]['quote']['bid'],1)
        self.assertEqual(result['rows'][0]['quote']['feed'],'frozen')
        self.assertFalse(result['rows'][0]['quote']['executable_reference'])
        self.assertEqual(ib.cancelled,[1])

    async def test_sdk_default_is_not_evidence_of_live_feed(self):
        ticks=SimpleNamespace(bid=1,ask=2,bidSize=1,askSize=1,marketDataType=1,time=None)
        class IB:
            def __init__(self):self.errorEvent=Event()
            async def qualifyContractsAsync(self,*contracts):
                c=contracts[0];c.conId=1;c.localSymbol='SPY   991218P00550000';return [c]
            def reqMarketDataType(self,t):pass
            def reqMktData(self,*args):return ticks
            def cancelMktData(self,c):pass
        with patch('data_providers.providers.ibkr_option_data.asyncio.sleep',new=AsyncMock()):
            result=await ibkr_option_data.snapshots(IB(),['SPY991218P00550000'],2)
        self.assertEqual(result['rows'][0]['quote']['feed'],'unknown')
        self.assertFalse(result['rows'][0]['quote']['executable_reference'])
