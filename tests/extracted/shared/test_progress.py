"""test_progress.py — the progress file, whose whole job is to survive the run it describes.

`cat var/status/<name>.json` is how a killed download is picked up rather than restarted, so
the file has to be written atomically (a monitor reading it mid-write must not see half a
JSON document) and must never be the reason a run dies.
"""
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.shared import progress


class TestStatus(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        p = mock.patch.object(progress, "STATUS_DIR", pathlib.Path(self._tmp.name))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def read(self, name):
        return json.loads((pathlib.Path(self._tmp.name) / f"{name}.json").read_text())

    def test_it_records_where_the_run_is(self):
        progress.status("unit_test_task", ticker="META", done=3, total=8)
        got = self.read("unit_test_task")
        self.assertEqual((got["ticker"], got["done"], got["total"]), ("META", 3, 8))
        self.assertIn("updated_utc", got, "the stamp is how a reader knows it is not stale")

    def test_it_MERGES_rather_than_replacing(self):
        """Called after every item with only the fields that changed; a replace would drop
        the ones that did not."""
        progress.status("merge_task", ticker="META", done=1)
        progress.status("merge_task", done=2)
        got = self.read("merge_task")
        self.assertEqual((got["ticker"], got["done"]), ("META", 2))

    def test_a_corrupt_file_is_replaced_not_fatal(self):
        path = pathlib.Path(self._tmp.name) / "corrupt.json"
        path.write_text("{not json at all")
        progress.status("corrupt", done=1)
        self.assertEqual(self.read("corrupt")["done"], 1)

    def test_it_leaves_no_temp_files_behind(self):
        progress.status("tidy", done=1)
        leftovers = [p.name for p in pathlib.Path(self._tmp.name).iterdir() if ".tmp" in p.name]
        self.assertEqual(leftovers, [])

    def test_an_unwritable_directory_never_kills_the_run(self):
        """It runs between units of work, so a full disk must not end the multi-hour download
        whose status it was failing to write."""
        with mock.patch.object(progress, "STATUS_DIR", pathlib.Path("/proc/nonexistent/nope")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                progress.status("unit_test_task", done=1)   # must not raise
        self.assertIn("the run continues", buf.getvalue(), "and it says so rather than hiding")
