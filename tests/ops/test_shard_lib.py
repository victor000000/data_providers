"""Tests for the shard process wrapper and its CLI surface.

The invariant that makes scaling safe lives here: each shard gets its OWN --staging file and
its OWN --client-id. DuckDB allows one writer per file, so two shards sharing a staging key
would not merely be wasteful — the second would fail to open, and if the client-id collided
the gateway would drop one of them. Both are asserted rather than assumed.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.ops import shard_lib as SL


class TestParseArgs(unittest.TestCase):
    def _parse(self, *argv):
        old = sys.argv
        sys.argv = ["adaptive_sweep.py", *argv]
        try:
            return SL.parse_args()
        finally:
            sys.argv = old

    def test_requires_shards_dir_and_script(self):
        with self.assertRaises(SystemExit):
            self._parse()

    def test_defaults_are_conservative(self):
        a = self._parse("--shards-dir", "/tmp/x", "--script", "apps/s.py")
        self.assertEqual((a.min, a.max), (1, 4))
        # `max 4` is the default ceiling, not a target: `gain` is what stops it climbing.
        self.assertGreater(a.gain, 0)
        self.assertLess(a.low, a.high, "low must be below high or the law is incoherent")

    def test_env_is_parsed_into_pairs(self):
        a = self._parse("--shards-dir", "/tmp/x", "--script", "s.py",
                        "--env", "IBKR_LIMIT=3600/600")
        self.assertEqual(dict(kv.split("=", 1) for kv in a.env), {"IBKR_LIMIT": "3600/600"})

    def test_env_value_may_contain_equals(self):
        # split("=", 1) not split("="): a value like `a=b` must survive intact.
        a = self._parse("--shards-dir", "/tmp/x", "--script", "s.py", "--env", "K=a=b")
        self.assertEqual(dict(kv.split("=", 1) for kv in a.env), {"K": "a=b"})


class TestShard(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path("/tmp/shardtest"); self.tmp.mkdir(exist_ok=True)
        self.u = self.tmp / "shard0.txt"; self.u.write_text("SPY\n")

    def test_staging_and_client_id_are_unique_per_shard(self):
        a = SL.Shard(0, self.u, "apps/s.py", {})
        b = SL.Shard(1, self.u, "apps/s.py", {})
        self.assertNotEqual(a.log, b.log)
        self.assertEqual(a.idx, 0)
        self.assertEqual(b.idx, 1)

    def test_not_alive_before_start(self):
        s = SL.Shard(0, self.u, "apps/s.py", {})
        self.assertFalse(s.alive())
        self.assertIsNone(s.proc)

    def test_stop_on_a_never_started_shard_is_a_noop(self):
        # Must not raise or signal a process group it does not own.
        SL.Shard(0, self.u, "apps/s.py", {}).stop()


if __name__ == "__main__":
    unittest.main()
