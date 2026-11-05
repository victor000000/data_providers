"""Tests for shard_link.link_up — the gateway probe that overrides the logs.

This guards the 2026-10-11 failure: the gateway refused every port from 05:14 and the sweep
ran until 07:53 reporting `processed=2 errors=0 ratio=0.0%`, because a shard on a dead link
still logs the occasional "no bars" and the controller counts that as work. The probe is the
signal that cannot be faked, so what it must never do is RAISE: a supervisor taken down by its
own health check is worse off than one that is merely blind.
"""
import pathlib
import socket
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.providers import tws
from data_providers.ops import shard_link as SL


def only_port(open_port):
    """A fake create_connection that answers on exactly one port."""
    def _connect(addr, timeout=None):
        if addr[1] != open_port:
            raise ConnectionRefusedError(111, "Connection refused")
        return mock.MagicMock()
    return _connect


class TestLinkUp(unittest.TestCase):
    def test_the_configured_port_is_asked_first(self):
        # IB_PORT exists so the user can point a session at TWS desktop; a probe that tried
        # its own hardcoded order would disagree with what the downloaders dial.
        seen = []

        def _connect(addr, timeout=None):
            seen.append(addr)
            raise ConnectionRefusedError(111, "Connection refused")

        with mock.patch.object(SL.socket, "create_connection", _connect):
            SL.link_up()
        self.assertEqual(seen[0], (tws.HOST, tws.PORT))

    def test_the_first_answering_port_wins(self):
        with mock.patch.object(SL.socket, "create_connection", only_port(7496)):
            self.assertEqual(SL.link_up(), 7496)

    def test_all_ports_refused_is_none_not_an_exception(self):
        refused = mock.Mock(side_effect=ConnectionRefusedError(111, "refused"))
        with mock.patch.object(SL.socket, "create_connection", refused):
            self.assertIsNone(SL.link_up())

    def test_an_unresolvable_host_counts_as_down(self):
        # socket.gaierror IS an OSError; a probe catching only ConnectionRefusedError would
        # raise here and kill the supervisor it was added to protect.
        bad = mock.Mock(side_effect=socket.gaierror(-2, "Name or service not known"))
        with mock.patch.object(SL.socket, "create_connection", bad):
            self.assertIsNone(SL.link_up())

    def test_an_explicit_port_list_is_honoured(self):
        with mock.patch.object(SL.socket, "create_connection", only_port(4242)):
            self.assertEqual(SL.link_up(ports=[4242]), 4242)

    def test_every_default_port_is_tried(self):
        tried = []

        def _connect(addr, timeout=None):
            tried.append(addr[1])
            raise ConnectionRefusedError(111, "refused")

        with mock.patch.object(SL.socket, "create_connection", _connect):
            SL.link_up()
        self.assertEqual(set(tried), {tws.PORT, 7496, 7497, 4001, 4002})


if __name__ == "__main__":
    unittest.main()
