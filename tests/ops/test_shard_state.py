"""Tests for shard_state — has this shard already finished its pass?

The distinction this file exists for: a `done:` from a PREVIOUS run must not count. If it
did, a sweep restarted after a crash would skip a shard that had finished one pass and then
died, and the symbols it never reached would silently never be fetched — a gap created by
the very mechanism meant to save time.
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.ops import shard_state as S  # noqa: E402


def log(text):
    p = pathlib.Path(tempfile.mkdtemp()) / "shard0.log"
    p.write_text(text)
    return p


class TestShardState(unittest.TestCase):
    def test_a_completed_run_is_finished(self):
        p = log("=== shard 0 start 2026-10-12T12:00:00Z ===\n"
                "A: skip (x -> y, 500 rows)\n"
                "done: 36 fetched, 1020 skipped, 362 failed\n")
        self.assertTrue(S.finished(p))

    def test_a_run_still_going_is_not_finished(self):
        p = log("=== shard 0 start 2026-10-12T12:00:00Z ===\n"
                "A: skip (x -> y, 500 rows)\n"
                "B: +12 rows\n")
        self.assertFalse(S.finished(p))

    def test_a_PREVIOUS_runs_done_does_not_count(self):
        # The trap: shard finished a pass, then was restarted and died mid-run. Reading the
        # old `done:` would skip it forever, and its unreached symbols with it.
        p = log("=== shard 0 start 2026-10-12T01:00:00Z ===\n"
                "done: 36 fetched, 1020 skipped, 362 failed\n"
                "=== shard 0 start 2026-10-12T12:00:00Z ===\n"
                "A: skip (x -> y, 500 rows)\n")
        self.assertFalse(S.finished(p))

    def test_missing_or_empty_log_is_not_finished(self):
        self.assertFalse(S.finished(pathlib.Path("/nonexistent/shard0.log")))
        self.assertFalse(S.finished(log("")))

    def test_a_marker_only_log_is_not_finished(self):
        self.assertFalse(S.finished(log("=== shard 0 start 2026-10-12T12:00:00Z ===\n")))

    def test_done_must_be_the_LAST_line_not_merely_present(self):
        # `done:` is printed once, at the very end. A run that printed it and then kept
        # logging (a supervisor restart, a late flush) has NOT finished this pass.
        p = log("=== shard 0 start 2026-10-12T12:00:00Z ===\n"
                "done: 1 fetched\n"
                "B: +12 rows\n")
        self.assertFalse(S.finished(p))

    def test_a_symbol_named_done_is_not_the_summary(self):
        # The log is full of symbol names; one of them could begin a line with "done:".
        p = log("=== shard 0 start 2026-10-12T12:00:00Z ===\n"
                "done: +12 rows (2024-10-10 -> 2026-10-09, last=1.0)\n")
        self.assertFalse(S.finished(p), "a symbol line is not the downloader's summary")

    def test_blank_lines_after_the_summary_do_not_hide_it(self):
        p = log("=== shard 0 start 2026-10-12T12:00:00Z ===\n"
                "done: 36 fetched, 1020 skipped, 362 failed\n\n\n")
        self.assertTrue(S.finished(p))


if __name__ == "__main__":
    unittest.main()
