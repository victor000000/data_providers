import os

import duckdb
import pytest

from data_providers.jobs.trades.capture_exports import capture
from data_providers.jobs.trades.import_cn_tracker import refresh


def setup_home(home):
    with duckdb.connect(str(home / "trades.duckdb")) as c:
        c.execute("CREATE TABLE positions(account TEXT,broker TEXT)")
        c.execute("INSERT INTO positions VALUES ('test-account','CN broker')")
        c.execute("CREATE TABLE fills(fill_id INTEGER)")
        c.execute("INSERT INTO fills VALUES (1)")
        c.execute("CREATE TABLE broker_positions(as_of TIMESTAMP,account TEXT,broker TEXT,underlying TEXT,"
                  "sec_type TEXT,symbol TEXT,expiry DATE,strike DOUBLE,call_put TEXT,currency TEXT,qty DOUBLE,"
                  "PRIMARY KEY(as_of,account,symbol,expiry,strike,call_put))")
        c.execute("CREATE TABLE account_daily(day DATE,account TEXT,currency TEXT,net_liq DOUBLE,"
                  "margin_used DOUBLE,available DOUBLE,risk_ratio DOUBLE,source TEXT,notes TEXT)")


def test_extensionless_tracker_capture_and_replay_do_not_create_executions(tmp_path):
    src, home = tmp_path / "source", tmp_path / "home"
    src.mkdir()
    header = "用户代码,交易所,持仓合约,买卖,总仓,开仓均价,持仓Delta,到期日\n"
    (src / "unnamed-old").write_bytes((header + "test-account,郑商所,MA701C3750,买,1,35.5,0.1,20261211\n").encode("gb18030"))
    os.utime(src / "unnamed-old", (1, 1))
    (src / "unnamed-new").write_bytes((header + "test-account,郑商所,MA701C3750,买,35,35.5,5.443,20261211\n"
                                      + ",,合计1条,,35,,,\n").encode("gb18030"))
    (src / "funds").write_bytes(("用户代码,动态权益,占用保证金,可用资金,风险度,参考风险度\n"
                                 "test-account,10000,9900,100,99%,113%\n").encode("gb18030"))
    captured = capture(src, home)
    assert {x["kind"] for x in captured} == {"cn_tracker_positions", "cn_tracker_account"}
    assert capture(src, home) == []
    setup_home(home)
    for _ in range(2):
        assert refresh(home)["fills_created"] == 0
    with duckdb.connect(str(home / "trades.duckdb"), read_only=True) as c:
        assert c.execute("SELECT symbol,qty FROM broker_positions").fetchall() == [("MA701C3750", 35)]
        assert c.execute("SELECT count(*) FROM fills").fetchone()[0] == 1
        assert c.execute("SELECT count(*),max(risk_ratio) FROM account_daily").fetchone() == (1, .99)
        assert c.execute("SELECT qty,avg_price FROM broker_position_costs").fetchone() == (35, 35.5)


def test_unknown_broker_account_is_not_guessed(tmp_path):
    src, home = tmp_path / "source", tmp_path / "home"
    src.mkdir()
    (src / "unnamed").write_text("用户代码,持仓合约,总仓,开仓均价,持仓Delta,买卖,到期日\n"
                                 "unknown,MA701P2900,2,81.5,0.4,卖,20261211\n")
    capture(src, home)
    setup_home(home)
    with pytest.raises(ValueError, match="broker mapping"):
        refresh(home)
    with duckdb.connect(str(home / "trades.duckdb"), read_only=True) as c:
        assert c.execute("SELECT count(*) FROM broker_positions").fetchone()[0] == 0
