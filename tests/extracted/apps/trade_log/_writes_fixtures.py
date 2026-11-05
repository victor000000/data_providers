"""The fake connection and row builders shared by the two schwab_writes tests."""
import datetime as dt


class FakeCon:
    """Records every statement; answers the two dedupe SELECTs from `already`."""

    def __init__(self, already=()):
        self.already = set(already)
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append(("execute", sql, params))
        return self

    def fetchall(self):
        return [(n,) for n in self.already]

    def executemany(self, sql, rows):
        # The WHOLE statement, not just the table: the assertion that matters is that
        # the placeholder count equals the values in each row, and that lives in the SQL.
        self.calls.append(("executemany", sql, rows))


def fill(notes="STRANGLE x; net .85; BUY 82C"):
    return {"ts_utc": dt.datetime(2026, 10, 6, 13, 50), "ts_orig": "10/6/26 21:50:47",
            "underlying": "TLT", "expiry": dt.date(2026, 11, 20), "strike": 82.0,
            "call_put": "C", "symbol": "TLT261120C00082000", "action": "BUY",
            "open_close": "CLOSE", "qty": 2, "price": 0.27, "notes": notes}


def order(notes="STRANGLE x; FILLED"):
    return {"ts_submit_utc": dt.datetime(2026, 10, 6, 13, 45), "underlying": "TLT",
            "action": "BUY", "qty": 2, "open_close": "CLOSE", "order_type": "STRANGLE",
            "limit_price": 0.85, "status": "FILLED", "notes": notes}


EMPTY_SUMMARY = {}
