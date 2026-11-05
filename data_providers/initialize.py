"""Initialize an independent runtime without copying live data or credentials."""
from importlib.resources import files
from pathlib import Path

import duckdb

from .data.schema import broker_snapshots, cn, cn_equities, futures, fx, history, indicators, log, option_research, reference


def initialize(home):
    home = Path(home).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    for name in ["plans", "staging", "var", "logs", "secrets", "market_data/data/raw",
                 "market_data/data/adjusted", "market_data/data/corporate_actions",
                 "market_data/indices", "market_data/sources", "market_data/export"]:
        (home / name).mkdir(parents=True, exist_ok=True)
    log.main(home / "trades.duckdb")
    with duckdb.connect(str(home / "trades.duckdb")) as con:
        broker_snapshots.ensure(con)
    reference.main(home / "reference.duckdb")
    with duckdb.connect(str(home / "market_history.duckdb")) as con:
        con.execute("SET TimeZone='UTC'")
        history.ensure(con, prefix="")
        futures.ensure(con)
        option_research.ensure(con)
        fx.ensure(con)
        indicators.ensure(con)
    with duckdb.connect(str(home / "cn_market.duckdb")) as con:
        con.execute("SET TimeZone='UTC'")
        cn.init(con)
        cn_equities.ensure(con)
        flow_ddl = next(ddl for ddl in history.DDL if "{p}option_flow_daily" in ddl)
        con.execute(flow_ddl.format(p=""))
    for item in files("data_providers.seed").iterdir():
        if item.name.endswith((".txt", ".csv")):
            destination = home / "plans" / item.name
            if not destination.exists():
                destination.write_bytes(item.read_bytes())
    return home
