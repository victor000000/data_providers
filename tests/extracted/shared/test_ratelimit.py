"""test_ratelimit.py — the gate itself: it must pace, and it must survive a corrupt slot.

The gate is a FILE shared by every process on the box, which is the point (in-process state
let three jobs each run at the full cap) and also the risk: a value nobody validates is a
value that can wedge every job at once.
"""
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.shared import ratelimit


class TestClaimSlot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        p = mock.patch.object(ratelimit, "GATE_DIR", pathlib.Path(self._tmp.name))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_the_first_claim_does_not_wait(self):
        self.assertLess(ratelimit.claim_slot("akshare", 0.5), 0.1)

    def test_the_second_claim_waits_one_interval(self):
        ratelimit.claim_slot("akshare", 0.5)
        self.assertGreater(ratelimit.claim_slot("akshare", 0.5), 0.3)

    def test_a_truncated_gate_file_reads_as_free(self):
        """A crash mid-write leaves a partial number; that is not a reason to refuse."""
        (pathlib.Path(self._tmp.name) / "akshare.gate").write_text("not-a-number")
        self.assertLess(ratelimit.claim_slot("akshare", 0.5), 0.1)

    def test_a_far_future_slot_is_RESET_not_slept_until(self):
        """flock protects against a dead PROCESS, not a bad VALUE: one clock step or one
        corrupt number would otherwise put every job on the box to sleep for years."""
        (pathlib.Path(self._tmp.name) / "akshare.gate").write_text(f"{time.time() + 10**9:.6f}")
        t0 = time.time()
        wait = ratelimit.claim_slot("akshare", 0.5)
        self.assertLess(time.time() - t0, 1.0, "it slept toward a corrupt slot")
        self.assertLess(wait, 1.0)

    def test_an_unusable_gate_warns_once_and_falls_back(self):
        """Falling back QUIETLY made this per-process pacing, so N concurrent jobs each
        believed it owned the full allowance -- the exact bug the gate exists to prevent."""
        with mock.patch.object(ratelimit, "GATE_DIR", pathlib.Path("/proc/nonexistent/nope")):
            ratelimit._gate_warned.discard("futu")
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ratelimit.claim_slot("futu", 0.01)
                ratelimit.claim_slot("futu", 0.01)      # second time: no repeat warning
            out = buf.getvalue()
        self.assertEqual(out.count("not equivalent"), 1)
