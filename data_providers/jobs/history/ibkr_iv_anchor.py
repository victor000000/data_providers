"""Source-wide freshness anchor independent of a shard's narrowed snapshot."""
import duckdb
from data_providers.data import db


def source_anchor(source, fallback, path=None):
    try:
        with duckdb.connect(str(path or db.HISTORY), read_only=True) as con:
            db.tune(con)
            newest = con.execute('SELECT max(day) FROM iv_history WHERE source=?',
                                 [source]).fetchone()[0]
        if newest is not None:
            from datetime import date
            return date.fromisoformat(str(newest)[:10])
    except duckdb.Error as exc:
        # A scoped snapshot cannot establish source freshness. Fail conservatively.
        print(f'IV source-wide anchor unavailable ({type(exc).__name__}); using {fallback}',
              flush=True)
    return fallback
