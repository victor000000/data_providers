"""A rolling minute feed must never erase observations outside its returned window."""
import datetime as dt
import unittest
import duckdb
import pandas as pd
from data_providers.jobs.trades.cn_minute_store import store_minute_frame


class CNMinuteRefreshTests(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(':memory:')
        self.con.execute('''CREATE TABLE underlying_minutes (
            ts_utc TIMESTAMP, symbol TEXT, open DOUBLE, high DOUBLE, low DOUBLE,
            close DOUBLE, volume DOUBLE, oi DOUBLE, source TEXT, CHECK(high >= low))''')
        self.day = dt.date(2026, 10, 16)
        self.ts = dt.datetime(2026, 10, 16, 1, 0)
        self.con.execute("INSERT INTO underlying_minutes VALUES (?, 'RU2701', 10,11,9,10,1,1,'sina_minute')", [self.ts])

    def tearDown(self):
        self.con.close()

    def bars(self, *times, low=9):
        return pd.DataFrame([dict(dt=t, open=10, high=11, low=low, close=10.5,
                                  volume=2, hold=3) for t in times])

    def refresh(self, frame, **kwargs):
        return store_minute_frame(self.con, 'RU2701', self.day,
                                  dt.time(9), dt.time(10), frame, **kwargs)

    def held(self):
        return self.con.execute('SELECT ts_utc, close, source FROM underlying_minutes ORDER BY 1').fetchall()

    def test_empty_or_expired_feed_preserves_saved_window(self):
        before = self.held()
        for frame in [pd.DataFrame(), self.bars('2026-10-17 09:00:00')]:
            self.assertEqual(self.refresh(frame), 0)
            self.assertEqual(self.held(), before)

    def test_partial_refresh_preserves_unreturned_bars_and_is_idempotent(self):
        frame = self.bars('2026-10-16 09:01:00', '2026-10-16 09:01:00')
        for _ in range(2):
            self.assertEqual(self.refresh(frame), 1)
            self.assertEqual(self.held(), [(self.ts, 10., 'sina_minute'),
                (self.ts + dt.timedelta(minutes=1), 10.5, 'sina_minute')])

    def test_invalid_rows_and_database_failure_preserve_history(self):
        before = self.held()
        for low in ['bad-number', float('nan'), 12]:
            with self.assertRaises(Exception):
                self.refresh(self.bars('2026-10-16 09:00:00', low=low))
            self.assertEqual(self.held(), before)
        self.assertEqual(self.refresh(self.bars('2026-10-16 09:00:00')), 1)
        self.assertEqual(self.held()[0][1], 10.5)

    def test_full_day_keeps_previous_evening_and_preserves_other_sources(self):
        self.con.execute("INSERT INTO underlying_minutes SELECT ts_utc,symbol,open,high,low,close,volume,oi,'broker' FROM underlying_minutes")
        self.assertEqual(self.refresh(self.bars('2026-10-15 21:01:00',
                                               '2026-10-16 09:00:00'), full_day=True), 2)
        self.assertEqual(len(self.held()), 3)
        self.assertIn((self.ts, 10., 'broker'), self.held())


    def test_partial_insert_failure_rolls_back_deleted_bar(self):
        before = self.held()
        self.con.execute('CREATE UNIQUE INDEX one_symbol ON underlying_minutes(symbol)')
        frame = self.bars('2026-10-16 09:00:00', '2026-10-16 09:01:00')
        with self.assertRaises(duckdb.ConstraintException):
            self.refresh(frame)
        self.assertEqual(self.held(), before)
