"""Normalize receipted CN tracker snapshots; never infer executions from positions."""
import csv
import io
import json
import re
from datetime import datetime

import duckdb

from data_providers.data.schema import broker_costs
from data_providers.runtime import data_home


def broker_for(con, account):
    rows = con.execute("SELECT DISTINCT broker FROM positions WHERE account=?", [account]).fetchall()
    if len(rows) != 1 or not rows[0][0]:
        raise ValueError("CN tracker account needs an unambiguous existing broker mapping")
    return rows[0][0]


def refresh(home):
    result = {"position_rows": 0, "account_rows": 0, "fills_created": 0,
              "timestamp_basis": "export file modified UTC; market event time unknown"}
    with duckdb.connect(str(home / "trades.duckdb")) as con:
        receipts = {}
        for kind in ("cn_tracker_positions", "cn_tracker_account"):
            row = con.execute("SELECT sha256,modified_utc,encoding,raw_content FROM broker_exports "
                              "WHERE kind=? ORDER BY modified_utc DESC,filename DESC LIMIT 1", [kind]).fetchone()
            if row:
                receipts[kind] = (row, list(csv.DictReader(io.StringIO(row[3].decode(row[2])))))
        con.execute("BEGIN")
        try:
            if "cn_tracker_positions" in receipts:
                receipt, rows = receipts["cn_tracker_positions"]
                for row in rows:
                    symbol = row["持仓合约"].strip()
                    match = re.fullmatch(r"([A-Za-z]+)(\d{3,4})-?([CP])-?(\d+(?:\.\d+)?)", symbol)
                    if not match:
                        if symbol.startswith("合计"):
                            continue
                        raise ValueError("Unrecognized CN tracker contract")
                    account = row["用户代码"].strip()
                    broker = broker_for(con, account)
                    direction = row["买卖"].strip()
                    if direction not in {"买", "卖"}:
                        raise ValueError("Unknown CN tracker position side")
                    qty = int(row["总仓"]) * (1 if direction == "买" else -1)
                    expiry = datetime.strptime(row["到期日"], "%Y%m%d").date()
                    underlying = match[1].upper() + match[2]
                    # Retain the vendor's actual expiry and contract spelling, including discrepancies.
                    values = [receipt[1], account, broker, underlying, "FUT_OPT", symbol,
                              expiry, float(match[4]), match[3], "CNY", qty]
                    con.execute("INSERT OR REPLACE INTO broker_positions VALUES (?,?,?,?,?,?,?,?,?,?,?)", values)
                    broker_costs.write(con, [[receipt[1], account, broker, underlying, expiry,
                                             float(match[4]), match[3], qty, float(row["开仓均价"]),
                                             "cn_tracker_header_v1:" + receipt[0]]])
                    result["position_rows"] += 1
            if "cn_tracker_account" in receipts:
                receipt, rows = receipts["cn_tracker_account"]
                for row in rows:
                    account = row["用户代码"].strip()
                    broker_for(con, account)
                    # File modification date is a snapshot date, not an exchange settlement date.
                    day = con.execute("SELECT CAST((? AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Shanghai' AS DATE)",
                                      [receipt[1]]).fetchone()[0]
                    source = "cn_tracker_header_v1"
                    con.execute("DELETE FROM account_daily WHERE account=? AND day=? AND source=?",
                                [account, day, source])
                    con.execute("INSERT INTO account_daily "
                                "(day,account,currency,net_liq,margin_used,available,risk_ratio,source,notes) "
                                "VALUES (?,?,?,?,?,?,?,?,?)",
                                [day, account, "CNY", float(row["动态权益"]), float(row["占用保证金"]),
                                 float(row["可用资金"]), float(row["风险度"].strip("%")) / 100, source,
                                 "File modified UTC: " + str(receipt[1]) + "; receipt " + receipt[0]
                                 + "; reference risk ratio " + row.get("参考风险度", "unknown")])
                    result["account_rows"] += 1
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return result


def main():
    print(json.dumps(refresh(data_home())))


if __name__ == "__main__":
    main()
