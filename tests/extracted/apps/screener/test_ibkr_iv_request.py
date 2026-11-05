"""Historical cancellations are failures, while true empty vendor series are valid."""
import unittest
from eventkit import Event
from data_providers.jobs.history.ibkr_iv_request import historical_iv_bars


class FakeIB:
    def __init__(self, event=None, error=None):
        self.errorEvent = Event()
        self.event, self.error = event, error

    def reqHistoricalData(self, contract, **kw):
        assert kw['timeout'] == 0  # IB.RequestTimeout must surface timeouts to the caller.
        if self.event:
            self.errorEvent.emit(1, self.event[0], self.event[1], contract)
        if self.error:
            raise self.error
        return []


class IVRequestTests(unittest.TestCase):
    def test_vendor_empty_is_not_transport_failure(self):
        ib = FakeIB((162, 'HMDS query returned no data'))
        self.assertEqual(historical_iv_bars(ib, 'SPY', '2 Y'), [])
        self.assertEqual(len(ib.errorEvent), 0)

    def test_cancel_pacing_and_link_errors_reach_retry_logic(self):
        for event in [(162, 'API historical data query cancelled'),
                      (162, 'Historical data pacing violation'),
                      (1100, 'Connectivity lost')]:
            ib = FakeIB(event)
            with self.assertRaises(RuntimeError):
                historical_iv_bars(ib, 'SPY', '2 Y')
            self.assertEqual(len(ib.errorEvent), 0)

    def test_timeout_is_not_empty_and_listener_always_removed(self):
        ib = FakeIB(error=TimeoutError('upstream did not answer'))
        with self.assertRaises(TimeoutError):
            historical_iv_bars(ib, 'SPY', '2 Y')
        self.assertEqual(len(ib.errorEvent), 0)
