"""An unavailable price feed must retain history and signal failure."""
import importlib.util
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd

import importlib
app = importlib.import_module("data_providers.warehouse.35_refresh_tail")


class PriceOutcomesTests(unittest.TestCase):
    def outcome(self, frame):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)
            original='15102026,100,110,90,100,100\n'
            for folder in ('raw','adjusted'):
                (root/folder).mkdir()
                (root/folder/'XYZ.csv').write_text(original)
            with patch.object(app.C,'ROOT',tmp), patch.object(app.C,'RAW',str(root/'raw')), patch.object(app.C,'ADJUSTED',str(root/'adjusted')), patch.object(app,'create_session'), patch.object(app.yf,'download',return_value=frame), patch.object(app.time,'sleep'), patch('sys.argv',['refresh']):
                result=app.main()
            self.assertEqual((root/'raw/XYZ.csv').read_text(),original)
            self.assertEqual((root/'adjusted/XYZ.csv').read_text(),original)
            return result

    def test_total_feed_failure_is_nonzero(self):
        self.assertEqual(self.outcome(pd.DataFrame()),1)

    def test_unchanged_verified_history_is_success(self):
        frame=pd.DataFrame({'Open':[100.],'High':[110.],'Low':[90.],'Close':[100.],
                            'Adj Close':[100.],'Volume':[100]},index=pd.to_datetime(['2026-10-15']))
        self.assertEqual(self.outcome(frame),0)


    def test_incomplete_new_adjusted_bar_keeps_both_saved_histories(self):
        frame=pd.DataFrame({'Open':[100.,101.],'High':[110.,111.],'Low':[90.,91.],
                            'Close':[100.,101.],'Adj Close':[100.,float('nan')],
                            'Volume':[100,100]},index=pd.to_datetime(['2026-10-15','2026-10-16']))
        self.assertEqual(self.outcome(frame),0)

    def test_full_rewrite_validates_factors_before_truncating(self):
        frame=pd.DataFrame({'Open':[100.],'High':[110.],'Low':[90.],'Close':[100.],
                            'Volume':[100]},index=pd.to_datetime(['2026-10-15']))
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'saved.csv';path.write_text('original history\n')
            factor=pd.Series([float('nan')],index=frame.index)
            with self.assertRaises(ValueError):
                app.rewrite(str(path),frame,factor)
            self.assertEqual(path.read_text(),'original history\n')

    def test_nonfinite_high_or_low_is_not_a_bar(self):
        for field in ['High','Low']:
            row=pd.Series({'Open':100.,'High':110.,'Low':90.,'Close':100.,'Volume':100})
            row[field]=float('nan')
            self.assertIsNone(app.row_of(pd.Timestamp('2026-10-16'),row))

    def test_empty_or_unusable_full_replacement_preserves_history(self):
        for highs in ([], [float('nan')], [0.]):
            frame=pd.DataFrame({'Open':[100.]*len(highs),'High':highs,
                                'Low':[90.]*len(highs),'Close':[100.]*len(highs),
                                'Volume':[100]*len(highs)},
                               index=pd.date_range('2026-10-15',periods=len(highs)))
            with self.subTest(highs=highs), tempfile.TemporaryDirectory() as tmp:
                path=pathlib.Path(tmp)/'saved.csv';path.write_text('original history\n')
                with self.assertRaises(ValueError):app.rewrite(str(path),frame,1.0)
                self.assertEqual(path.read_text(),'original history\n')

    def test_failed_publication_preserves_history_and_removes_temporary_file(self):
        frame=pd.DataFrame({'Open':[100.],'High':[110.],'Low':[90.],'Close':[100.],
                            'Volume':[100]},index=pd.to_datetime(['2026-10-15']))
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'saved.csv';path.write_text('original history\n')
            with patch.object(app.os,'replace',side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):app.rewrite(str(path),frame,1.0)
            self.assertEqual(path.read_text(),'original history\n')
            self.assertEqual(list(pathlib.Path(tmp).iterdir()),[path])

    def test_equal_row_count_does_not_allow_old_dates_to_disappear(self):
        frame=pd.DataFrame({'Open':[100.,100.],'High':[110.,110.],'Low':[90.,90.],
                            'Close':[100.,100.],'Volume':[100,100]},
                           index=pd.to_datetime(['2026-10-15','2026-10-16']))
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'saved.csv'
            original='14102026,100,110,90,100,100\n15102026,100,110,90,100,100\n'
            path.write_text(original)
            with self.assertRaisesRegex(ValueError,'loses stored dates'):
                app.rewrite(str(path),frame,1.0)
            self.assertEqual(path.read_text(),original)
