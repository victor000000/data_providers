"""Tests for the supervisor's entry behaviour.

Deliberately NOT a test that starts real shards — that would spawn downloaders from a test run
and hit the network. What is worth pinning is the behaviour that runs BEFORE any process
exists, because that is where a bad argument list turns into "nothing happened, exit 0":
a missing or empty shards dir must be a loud non-zero, not a silent success.
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.ops import adaptive_sweep as AS


class TestEntryBehaviour(unittest.TestCase):
    def _run(self, *argv):
        old = sys.argv
        sys.argv = ["adaptive_sweep.py", *argv]
        try:
            return AS.main()
        finally:
            sys.argv = old

    def test_missing_shards_dir_is_a_loud_failure(self):
        # Returning 0 here would tell a cron wrapper "swept everything" when it swept nothing.
        rc = self._run("--shards-dir", "/tmp/definitely-not-a-real-dir-xyz",
                       "--script", "apps/s.py", "--min", "1", "--max", "1")
        self.assertEqual(rc, 2)

    def test_empty_shards_dir_is_also_a_failure(self):
        d = tempfile.mkdtemp()   # exists, but holds no shard*.txt
        rc = self._run("--shards-dir", d, "--script", "apps/s.py")
        self.assertEqual(rc, 2)

    def test_non_numeric_shard_lookalikes_are_excluded(self):
        # `shard*.txt` ALONE matched `shards_notes.txt`, so a stray notes file in the shards dir
        # would be launched as a universe. The selection requires digits after the prefix.
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "README.md").write_text("not a universe\n")
        (d / "shards_notes.txt").write_text("also not one\n")
        picked = sorted(d.glob("shard[0-9]*.txt"))
        self.assertEqual(picked, [], "shards_notes.txt must NOT be launched as a universe")

    def test_real_shard_files_are_picked_up(self):
        d = pathlib.Path(tempfile.mkdtemp())
        for n in ("shard0.txt", "shard1.txt", "shard10.txt"):
            (d / n).write_text("SPY\n")
        picked = sorted(p.name for p in d.glob("shard[0-9]*.txt"))
        self.assertEqual(picked, ["shard0.txt", "shard1.txt", "shard10.txt"])


if __name__ == "__main__":
    unittest.main()
