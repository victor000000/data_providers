"""Bounded independent historical requests: a timeout cancels only its owner."""
import asyncio

from ib_async import util
from ib_async.objects import BarDataList


async def request_history(ib, contract, *, request_id, end, duration, bar_size,
                          what_to_show, timeout=180):
    bars = BarDataList()
    bars.reqId = request_id
    bars.contract = contract
    bars.endDateTime = end
    bars.durationStr = duration
    bars.barSizeSetting = bar_size
    bars.whatToShow = what_to_show
    bars.useRTH = True
    bars.formatDate = 1
    bars.keepUpToDate = False
    bars.chartOptions = []
    future = ib.wrapper.startReq(request_id, contract, container=bars)
    try:
        ib.client.reqHistoricalData(request_id, contract,
                                    util.formatIBDatetime(end), duration,
                                    bar_size, what_to_show, True, 1, False, [])
        await asyncio.wait_for(future, timeout)
        return bars
    except (asyncio.TimeoutError, asyncio.CancelledError):
        ib.client.cancelHistoricalData(request_id)
        raise
    finally:
        ib.wrapper._endReq(request_id)
