"""Broker quotes must keep the expiry in their identity."""
import datetime as dt
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from data_providers.providers import tws


class TestOptionMarks(unittest.TestCase):
    def test_the_same_strike_in_two_months_keeps_both_quotes(self):
        import ib_async
        nov, dec = dt.date(2026, 11, 20), dt.date(2026, 12, 18)
        ib = Mock()
        ib.qualifyContracts.side_effect = lambda *contracts: contracts
        ib.reqTickers.return_value = [
            SimpleNamespace(bid=1.9, ask=2.1, last=2., close=2.),
            SimpleNamespace(bid=7.9, ask=8.1, last=8., close=8.),
        ]
        with patch.object(ib_async, "IB", return_value=ib), patch.object(tws, "connect"):
            marks = tws.option_marks([("XYZ", nov, 100., "P"), ("XYZ", dec, 100., "P")])
        self.assertEqual(len(marks), 2)
        self.assertEqual(marks[("XYZ", nov, 100., "P")]["mid"], 2.)
        self.assertEqual(marks[("XYZ", dec, 100., "P")]["mid"], 8.)
        ib.disconnect.assert_called_once()
