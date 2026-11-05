import duckdb

from data_providers import MarketData
from data_providers.initialize import initialize


def test_fresh_runtime_matches_deployed_writers_and_preserves_user_configuration(tmp_path):
    home = tmp_path / "runtime"
    initialize(home)
    client = MarketData(home, private=True)
    for dataset in ["us_daily", "us_iv", "cn_daily", "cn_iv", "fills", "instruments"]:
        assert client.read(dataset) == []
    assert len(client.read("cn_products")) > 20
    assert "contract" in {row["name"] for row in client.schema("cn_option_bars")}
    assert "underlying" in {row["name"] for row in client.schema("cn_option_bars")}
    path = home / "plans/symbol_map.csv"
    path.write_text("user configuration")
    with duckdb.connect(str(home / "market_history.duckdb")) as con:
        con.execute("INSERT INTO underlying_daily(day,symbol,close,source) VALUES "
                    "('2026-10-16','MA',100,'test')")
    initialize(home)
    assert path.read_text() == "user configuration"
    assert client.read("us_daily")[0]["close"] == 100
