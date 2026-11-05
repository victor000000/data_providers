"""test_net.py — retry, and the fact that net's re-exports really are the same objects.

net.py is now the front door for three concerns: retry (its own), the rate gate
(ratelimit.py) and the progress files (progress.py). It re-exports the other two so the 26
modules doing `from data_providers.shared.net import throttle, status` keep working — and that only holds
while they are ALIASES. A copy would drift, and the copy is the one nobody tests, which is
the failure this file exists to catch.
"""
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.shared import net, progress, ratelimit


class TestRetry(unittest.TestCase):
    def test_a_success_never_retries(self):
        calls = []
        self.assertEqual(net.retry(lambda: calls.append(1) or "ok"), "ok")
        self.assertEqual(len(calls), 1)

    def test_it_retries_a_transient_failure_then_succeeds(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError("blip")
            return "ok"

        with mock.patch.object(net.time, "sleep"):
            self.assertEqual(net.retry(flaky, tries=4), "ok")
        self.assertEqual(len(calls), 3)

    def test_it_gives_up_with_the_LAST_error_in_the_message(self):
        def dead():
            raise ConnectionError("Failed to resolve 'stock2.finance.sina.com.cn'")

        with mock.patch.object(net.time, "sleep"):
            with self.assertRaises(RuntimeError) as cm:
                net.retry(dead, tries=3, what="M0")
        self.assertIn("M0", str(cm.exception))
        self.assertIn("resolve", str(cm.exception))

    def test_backoff_doubles_and_does_not_sleep_after_the_final_try(self):
        slept = []

        def boom():
            raise ValueError("x")

        with mock.patch.object(net.time, "sleep", slept.append):
            with self.assertRaises(RuntimeError):
                net.retry(boom, tries=4, base=2)
        self.assertEqual(slept, [1, 2, 4], "3 gaps for 4 tries, doubling")


class TestReExports(unittest.TestCase):
    def test_status_is_progress_s_own_object(self):
        self.assertIs(net.status, progress.status,
                      "a copy here would drift from the one the tests exercise")

    def test_throttle_paces_through_ratelimit_s_gate(self):
        """Not `net`'s own clock: it must go through the shared file gate."""
        with mock.patch.object(ratelimit, "claim_slot", return_value=0.0) as claimed:
            net.throttle("akshare")
        claimed.assert_called_once()

    def test_throttle_sleeps_for_the_wait_the_gate_returns(self):
        slept = []
        with mock.patch.object(ratelimit, "claim_slot", return_value=0.25):
            with mock.patch.object(net.time, "sleep", slept.append):
                net.throttle("akshare")
        self.assertEqual(slept, [0.25])

    def test_an_unconfigured_source_fails_loudly_rather_than_running_unpaced(self):
        with self.assertRaises(KeyError):
            net.throttle("no-such-source")
