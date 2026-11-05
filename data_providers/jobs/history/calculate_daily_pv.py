"""Calculate supported US/CN observed-bar PV families from a pinned release.

These local methods do not assert exact BRAIN equivalence or verified daily
calendar continuity. They do not derive missing data or splice continuous rolls.
"""
import fcntl
import json
from contextlib import nullcontext
from datetime import UTC, datetime

import duckdb

from data_providers import MarketData
from data_providers.data.schema.indicators import ensure
from data_providers.lake import ident, literal
from data_providers.runtime import data_home


def calculate(con, input_release, stamp, start='2014-01-01'):
    """Input bars must be unique per instrument/source/date, including warmup."""
    if con.execute('SELECT count(*) FROM (SELECT market,venue,instrument_id,source,day FROM inputs '
                   'GROUP BY ALL HAVING count(*)>1)').fetchone()[0]:
        raise ValueError('Duplicate source/instrument dates')
    con.execute('''CREATE TEMP TABLE changes AS SELECT *,
        lag(close) OVER w previous_close,lag(day) OVER w previous_day,
        CASE WHEN close>0 AND isfinite(close) AND lag(close) OVER w>0 AND isfinite(lag(close) OVER w)
             THEN close/(lag(close) OVER w)-1 ELSE NULL END observed_return
        FROM inputs WINDOW w AS(PARTITION BY market,venue,instrument_id,source ORDER BY day)''')
    con.execute('''CREATE TEMP TABLE features AS SELECT *,
        CASE WHEN count(volume) OVER v=20 THEN avg(volume) OVER v ELSE NULL END prior_volume_mean,
        CASE WHEN count(observed_return) OVER h=20 THEN stddev_samp(observed_return) OVER h*sqrt(252)
             ELSE NULL END realized_volatility
        FROM changes WINDOW v AS(PARTITION BY market,venue,instrument_id,source ORDER BY day
                                 ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING),
                            h AS(PARTITION BY market,venue,instrument_id,source ORDER BY day
                                 ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)''')
    ensure(con)
    con.execute('''INSERT INTO daily_indicators
        SELECT market,venue,instrument_id,symbol,contract_id,day,source,'observed_bar_pv_v1',
               f.field_id,f.value,f.units,NULL,?::TIMESTAMPTZ,?,
               CASE WHEN f.value IS NULL THEN 'warmup_or_missing_input;availability_unknown'
                    ELSE 'calendar_continuity_unverified;availability_unknown;brain_equivalence_unverified' END,
               json_object('previous_observation_day',previous_day,'price_convention',price_convention,
                           'annualization_observations',252,'hv_ddof',1,
                           'volume_window','20 prior observed bars; excludes current bar',
                           'contract_roll','partitioned by deliverable contract; no continuous futures',
                           'input_release',?)
        FROM features CROSS JOIN LATERAL (VALUES
             ('close_return_since_previous_observation',observed_return,'fraction'),
             ('prior_20_observation_mean_volume',prior_volume_mean,volume_unit),
             ('realized_volatility_20_observation',realized_volatility,'decimal_annualized_252'))
             f(field_id,value,units)
        WHERE day>=?::DATE''', [stamp, input_release, input_release, start])


def merge_stage(home, path, *, locked=False):
    lockpath = home / 'var/maintenance/writer.lock'
    lockpath.parent.mkdir(parents=True, exist_ok=True)
    with (nullcontext() if locked else lockpath.open('a')) as lock:
        if not locked:
            fcntl.flock(lock, fcntl.LOCK_EX)
        with duckdb.connect(str(home / 'market_history.duckdb')) as con:
            con.execute("SET TimeZone='UTC'")
            ensure(con)
            con.execute(f'ATTACH {literal(path)} AS staged (READ_ONLY)')
            columns = [r[1] for r in con.execute("PRAGMA table_info('daily_indicators')").fetchall()]
            keys = [r[1] for r in con.execute("PRAGMA table_info('daily_indicators')").fetchall() if r[5]]
            updates = ','.join(f'{ident(n)}=EXCLUDED.{ident(n)}' for n in columns if n not in keys)
            con.execute('BEGIN')
            try:
                con.execute('INSERT INTO daily_indicators SELECT * FROM staged.daily_indicators '
                            f"ON CONFLICT ({','.join(map(ident,keys))}) DO UPDATE SET {updates}")
                if con.execute('SELECT count(*) FROM (SELECT * FROM staged.daily_indicators EXCEPT '
                               'SELECT * FROM daily_indicators)').fetchone()[0]:
                    raise ValueError('Indicator verified merge failed')
                con.execute('COMMIT')
            except Exception:
                con.execute('ROLLBACK')
                raise


def build(home=None):
    m = MarketData(home, backend='parquet')
    stamp = datetime.now(UTC)
    run = stamp.strftime('daily-pv-%Y%m%dT%H%M%S%fZ')
    path = m.home / 'staging/indicators' / (run + '.duckdb')
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(path)) as con:
        con.execute("SET TimeZone='UTC'")
        con.execute('CREATE VIEW us_prices AS ' + m.release.query('universe', 'prices'))
        con.execute('CREATE VIEW cn_prices AS ' + m.release.query('cn', 'futures_daily'))
        con.execute('''CREATE TABLE inputs AS
            SELECT 'US' AS market,'UNSPECIFIED_US' AS venue,'US:EQUITY:'||symbol AS instrument_id,symbol,
                   NULL::VARCHAR AS contract_id,"date" AS day,'yahoo' AS "source",
                   CASE WHEN adj_close>0 AND isfinite(adj_close) THEN adj_close ELSE NULL END AS "close",
                   CASE WHEN volume>=0 THEN volume ELSE NULL END AS volume,
                   'shares' AS volume_unit,'latest_vendor_adjusted;historical_revision_availability_unknown' AS price_convention
            FROM us_prices WHERE symbol IN ('SPY','QQQ')
            UNION ALL
            SELECT 'CN',venue,'CN:'||venue||':FUT:'||upper(contract),product,upper(contract),day,source,
                   CASE WHEN close>0 AND isfinite(close) THEN close ELSE NULL END,
                   CASE WHEN volume>=0 AND isfinite(volume) THEN volume ELSE NULL END,
                   'contracts','source_native_deliverable_contract_close'
            FROM cn_prices WHERE venue='DCE' AND product='M' AND source='sina_contract'
              AND regexp_full_match(upper(contract),'M[0-9]{4}')''')
        calculate(con, m.release.id, stamp)
        summary = con.execute('''SELECT market,field_id,count(*),count(value),min(day),max(day),
                                    count(DISTINCT instrument_id)
                                FROM daily_indicators GROUP BY ALL ORDER BY ALL''').fetchall()
        if {r[0] for r in summary} != {'US','CN'}:
            raise ValueError('Both market input families must be present')
        con.execute('DROP VIEW us_prices');con.execute('DROP VIEW cn_prices')
    merge_stage(m.home, path)
    folder = m.home / 'var/maintenance' / run
    folder.mkdir()
    report = {'status':'verified_calculated_and_merged','input_release':m.release.id,'stage':str(path),
              'market_field_counts_rows_valid_first_last_instruments':summary,
              'method_id':'observed_bar_pv_v1','brain_equivalence_verified':False,
              'limitations':['Observed-bar intervals are not verified consecutive calendar sessions',
                             'US adjusted history is the vendor snapshot, not proven historical-as-of factors',
                             'CN scope is DCE M deliverable futures; CN equity/ETF inputs remain pending',
                             'Source available_at is unknown; strict point-in-time consumers must reject it',
                             'This is an initial calculation graph; broad US/CN and option-model families remain pending']}
    (folder/'report.json').write_text(json.dumps(report,default=str,indent=2)+'\n')
    return folder, report


if __name__=='__main__':
    folder,report=build(data_home())
    print(json.dumps({'path':str(folder),**report},default=str))
