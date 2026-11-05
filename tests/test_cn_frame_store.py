import datetime as dt

import duckdb
import pandas as pd
import pytest

from data_providers.data.schema import cn, history
from data_providers.jobs.data_ops.download_cn_option_bars import write_frames


def setup_store():
    con = duckdb.connect()
    cn.init(con)
    con.execute("""INSERT INTO option_bars
        (source,contract,day,expiry,iv,close,venue,product) VALUES
        ('akshare_czce_ma','MA701C2500','2026-10-16','2026-12-31',0.3,11,'CZCE','MA')""")
    return con


def publish(con, oi):
    frame = pd.DataFrame([{'day': '2026-10-16', 'code': 'MA701C2500',
        'open': 10, 'high': 12, 'low': 9, 'close': 11, 'volume': 3, 'oi': oi}])
    return write_frames(con, frame, 'akshare_czce_ma', 'MA', 'day', 'code',
        'open', 'high', 'low', 'close', 'volume', 'oi', venue='CZCE')


def test_frame_repairs_expiry_preserves_known_iv_and_is_idempotent():
    con = setup_store()
    for _ in range(2):
        assert publish(con, 7) == (1, 1)
    assert con.execute('select count(*),max(expiry),max(iv),max(oi) from option_bars').fetchone() == (
        1, dt.date(2026, 12, 11), 0.3, 7)
    assert con.execute('select count(*),max(call_oi),max(call_vol) from option_flow_daily').fetchone() == (1, 7, 3)
    con.close()


def test_flow_failure_rolls_back_bar_enrichment():
    con = setup_store()
    con.execute('DROP TABLE option_flow_daily')
    con.execute(history.DDL[1].format(p='').replace('call_oi DOUBLE,', 'call_oi DOUBLE CHECK(call_oi>=0),'))
    with pytest.raises(duckdb.ConstraintException):
        publish(con, -7)
    assert con.execute('select expiry,oi from option_bars').fetchone() == (dt.date(2026, 12, 31), None)
    assert con.execute('select count(*) from option_flow_daily').fetchone()[0] == 0
    con.close()
