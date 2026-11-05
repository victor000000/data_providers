"""Persistent market-data-only IBKR connection, independent of trading accounts."""
import asyncio
import os
import threading
from ib_async import IB, StartupFetch
from . import ibkr_option_data
from .option_quote import SourceUnavailable

OPERATIONS = ('expiries', 'snapshots', 'chain', 'underlying')


class ReadOnlyIBKR:
    def __init__(self):
        self.loop = None
        self.ib = None
        self.start_lock = threading.Lock()
        self.info = dict(role='market data only', readonly=True, connected=False,
                         host='127.0.0.1', port=int(os.getenv('IB_MARKET_PORT', '4001')))

    def market(self, operation, *args):
        if operation not in OPERATIONS:
            raise ValueError('Read-only market operation required')
        with self.start_lock:
            if self.loop is None:
                self.loop = asyncio.new_event_loop()
                threading.Thread(target=self._run, daemon=True).start()
        task = asyncio.run_coroutine_threadsafe(self._request(operation, args), self.loop)
        try:
            return task.result(timeout=40)
        except TimeoutError as exc:
            task.cancel()
            raise SourceUnavailable('IBKR read-only market request timed out') from exc

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.lock = asyncio.Lock()
        self.loop.run_forever()

    async def _request(self, operation, args):
        async with self.lock:
            if self.ib is None:
                self.ib = IB()
            if not self.ib.isConnected():
                self.info['connected'] = False
                try:
                    await self.ib.connectAsync('127.0.0.1', self.info['port'], clientId=194,
                                               readonly=True, timeout=6, fetchFields=StartupFetch(0))
                except Exception as exc:
                    raise SourceUnavailable(f"IBKR market-data gateway unavailable on port {self.info['port']}") from exc
            self.info.update(connected=True, accounts=self.ib.managedAccounts())
            return await getattr(ibkr_option_data, operation)(self.ib, *args)

    def status(self):
        return dict(self.info, connected=bool(self.ib and self.ib.isConnected()))
