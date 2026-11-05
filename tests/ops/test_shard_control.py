"""Tests for the shard scaling law and the health signals it reads.

Worth real tests because both halves were wrong in ways that failed QUIETLY. The error regex
counted benign "returned no data" responses as faults, which pushed a healthy run past its
scale-down threshold; and the control law scaled to max on error ratio alone while throughput
never moved. Neither raised anything — a controller that misreads its inputs just does the
wrong thing more confidently.
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.ops import shard_control as SC

BASE = dict(n_running=2, n_started=2, n_total=4, ratio=0.0, d_done=24, rate_before=None,
            saturated=False, low=0.06, high=0.18, gain=0.15, min_shards=1, max_shards=4)


def decide(**over):
    return SC.decide(**{**BASE, **over})


class TestControlLaw(unittest.TestCase):
    def test_scales_up_when_healthy_and_headroom_exists(self):
        action, _, rate, _ = decide()
        self.assertEqual(action, SC.UP)
        self.assertEqual(rate, 24, "the scale-up baseline must be recorded for the next test")

    def test_scales_down_when_errors_exceed_high(self):
        # Asking for MORE work from a vendor that is refusing work is the wrong response.
        action, why, rate, sat = decide(ratio=0.30)
        self.assertEqual(action, SC.DOWN)
        self.assertIsNone(rate, "a back-off invalidates the scale-up baseline")
        self.assertFalse(sat)

    def test_never_scales_below_min_or_above_max(self):
        self.assertEqual(decide(n_running=1, ratio=0.99)[0], SC.HOLD)
        self.assertEqual(decide(n_running=4, ratio=0.0)[0], SC.HOLD)

    def test_saturates_when_a_scale_up_bought_nothing(self):
        # THE BUG. Measured 2026-10-10: IBKR gave 24 fetches/min at 1, 2, 3 AND 4 shards, so a
        # scale-up that gained 0% must stop scaling rather than climb to max forever.
        action, why, _, sat = decide(rate_before=24, d_done=24)
        self.assertEqual(action, SC.HOLD)
        self.assertTrue(sat)
        self.assertIn("SATURATED", why)

    def test_still_scales_when_the_gain_is_real(self):
        action, _, rate, sat = decide(rate_before=10, d_done=24)
        self.assertEqual(action, SC.UP)
        self.assertFalse(sat)
        self.assertEqual(rate, 24)

    def test_does_not_start_more_shards_than_exist(self):
        action, _, _, _ = decide(n_running=2, n_started=4, n_total=4)
        self.assertEqual(action, SC.HOLD)

    def test_saturation_is_sticky_until_a_backoff_resets_it(self):
        _, _, _, sat = decide(rate_before=24, d_done=24)
        self.assertTrue(sat)
        action, _, _, sat2 = decide(saturated=sat, rate_before=24, d_done=24)
        self.assertEqual(action, SC.HOLD)
        self.assertTrue(sat2)


class TestHealthSignals(unittest.TestCase):
    def _log(self, text):
        # counts() measures from the start marker, so a fixture must carry one (Shard.start()).
        d = tempfile.mkdtemp()
        p = pathlib.Path(d) / "shard0.log"
        p.write_text(f"=== shard 0 start 2026-10-10T13:17:00Z ===\n{text}")
        return p

    def test_benign_no_data_is_not_an_error(self):
        # "returned no data" is a fact about the symbol. Counting it as a fault made a healthy
        # run read 14-15% and would have throttled it.
        log = self._log("A: no bars\nError 162, reqId 1: HMDS query returned no data\n")
        done, err, nobars = SC.counts(log)
        self.assertEqual(err, 0, "benign no-data must not count as an error")
        self.assertEqual(nobars, 1)

    def test_real_pacing_is_an_error(self):
        log = self._log("Error 162, reqId 2: HMDS query cancelled\n")
        _, err, _ = SC.counts(log)
        self.assertEqual(err, 1)

    def test_connectivity_errors_count(self):
        log = self._log("Error 1102: connectivity broken\nError 1100: lost\n")
        self.assertEqual(SC.counts(log)[1], 2)

    def test_missing_log_is_zero_not_a_crash(self):
        self.assertEqual(SC.counts(pathlib.Path("/nonexistent/x.log")), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
