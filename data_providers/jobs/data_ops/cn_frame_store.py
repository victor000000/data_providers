"""Atomically publish one CN option frame and its exchange-volume aggregates."""
import pandas as pd

BAR_COLUMNS = ('source contract underlying expiry strike call_put day open high low close '
               'volume vwap venue product oi settle iv delta').split()
FLOW_COLUMNS = ('day underlying source call_oi put_oi iv_rank iv_percentile hv30 hv365 '
                'call_vol put_vol pcr_vol pcr_oi gex_proxy method note').split()


def persist(con, bars, flows):
    registered = []
    try:
        if bars:
            con.register('_cn_frame_bars', pd.DataFrame.from_records(bars, columns=BAR_COLUMNS))
            registered.append('_cn_frame_bars')
        if flows:
            con.register('_cn_frame_flows', pd.DataFrame.from_records(flows, columns=FLOW_COLUMNS))
            registered.append('_cn_frame_flows')
        con.execute('BEGIN')
        try:
            if bars:
                cols = ','.join(BAR_COLUMNS)
                # Preserve the existing enrichment policy: absent vendor fields do not
                # erase fields already held, while corrected expiries replace old ones.
                con.execute(f'''INSERT INTO option_bars ({cols})
                    SELECT {cols} FROM _cn_frame_bars
                    ON CONFLICT (source, contract, day) DO UPDATE SET
                        expiry=EXCLUDED.expiry,
                        venue=COALESCE(EXCLUDED.venue, option_bars.venue),
                        product=COALESCE(EXCLUDED.product, option_bars.product),
                        oi=COALESCE(EXCLUDED.oi, option_bars.oi),
                        settle=COALESCE(EXCLUDED.settle, option_bars.settle),
                        iv=COALESCE(EXCLUDED.iv, option_bars.iv),
                        delta=COALESCE(EXCLUDED.delta, option_bars.delta)''')
            if flows:
                cols = ','.join(FLOW_COLUMNS)
                con.execute(f'''INSERT OR REPLACE INTO option_flow_daily ({cols})
                    SELECT {cols} FROM _cn_frame_flows''')
            con.execute('''UPDATE option_bars SET
                venue=COALESCE(venue,upper(split_part(source,'_',2))),
                product=COALESCE(product,upper(split_part(source,'_',3)))
                WHERE venue IS NULL''')
            con.execute('COMMIT')
        except BaseException:
            con.execute('ROLLBACK')
            raise
    finally:
        for name in registered:
            con.unregister(name)
