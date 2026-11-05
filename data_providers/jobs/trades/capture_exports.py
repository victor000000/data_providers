"""Capture newest broker exports by type, including layouts without a normalized loader.

Immutable source receipts complement normalized fills; they do not fabricate fills
or assume the YC and KQ exports represent different trades.
"""
import csv
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from data_providers.runtime import data_home, trading_logs_dir

KINDS = tuple(f"{vendor}_{kind}" for vendor in ("kq", "yc")
              for kind in ("orders", "trades", "positions", "account"))


def header_kind(path):
    """Recognize unnamed CN snapshots by layout without guessing their broker program."""
    if path.suffix.lower() not in ("", ".csv", ".txt"):
        return None
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            header = set(next(csv.reader(io.StringIO(raw.decode(encoding))), []))
            break
        except (UnicodeDecodeError, csv.Error):
            continue
    else:
        return None
    layouts = {
        "cn_tracker_positions": {"用户代码", "持仓合约", "总仓", "开仓均价", "持仓Delta"},
        "cn_tracker_account": {"用户代码", "动态权益", "占用保证金", "可用资金"},
        "cn_tracker_orders": {"委托合约", "买卖", "开平", "报单时间", "挂单状态"},
    }
    return next((kind for kind, fields in layouts.items() if fields <= header), None)


def capture(source, home):
    source, home = Path(source), Path(home)
    if not source.is_dir():
        raise FileNotFoundError(f"Trading export source is unavailable: {source}")
    files = []
    for kind in KINDS:
        candidates = list(source.glob(f"{kind}_*.csv"))
        if candidates:
            files.append((kind, max(candidates, key=lambda p: (p.stat().st_mtime_ns, p.name))))
    identified = {}
    for path in source.iterdir():
        if not path.is_file() or path.name.startswith((".", "~$")):
            continue
        kind = header_kind(path)
        if kind and (kind not in identified or (path.stat().st_mtime_ns, path.name) > (
                identified[kind].stat().st_mtime_ns, identified[kind].name)):
            identified[kind] = path
    files.extend(sorted(identified.items()))
    if not files:
        raise FileNotFoundError("No KQ/YC daily exports found")
    home.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(home / "trades.duckdb")) as con:
        con.execute("SET TimeZone='UTC'")
        con.execute("CREATE TABLE IF NOT EXISTS broker_exports ("
                    "kind TEXT, sha256 TEXT, filename TEXT, modified_utc TIMESTAMP, "
                    "captured_utc TIMESTAMP, encoding TEXT, row_count INTEGER, "
                    "raw_content BLOB, PRIMARY KEY(kind, sha256))")
        con.execute("CREATE TABLE IF NOT EXISTS broker_export_rows ("
                    "kind TEXT, sha256 TEXT, row_number INTEGER, payload JSON, "
                    "PRIMARY KEY(kind, sha256, row_number))")
        captured = []
        con.execute("BEGIN")
        try:
            for kind, path in files:
                raw = path.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                if con.execute("SELECT 1 FROM broker_exports WHERE kind=? AND sha256=?",
                               [kind, digest]).fetchone():
                    continue
                for encoding in ("utf-8-sig", "gb18030"):
                    try:
                        text = raw.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    raise ValueError(f"Unknown encoding for {path.name}")
                reader = csv.DictReader(io.StringIO(text))
                if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                    raise ValueError(f"Invalid or duplicate headers in {path.name}")
                rows = list(reader)
                if any(None in row for row in rows):
                    raise ValueError(f"Malformed row in {path.name}")
                con.execute("INSERT INTO broker_exports VALUES (?,?,?,?,?,?,?,?)",
                            [kind, digest, path.name, datetime.fromtimestamp(path.stat().st_mtime, UTC),
                             datetime.now(UTC), encoding, len(rows), raw])
                if rows:
                    con.executemany("INSERT INTO broker_export_rows VALUES (?,?,?,?)",
                                    [(kind, digest, index, json.dumps(row, ensure_ascii=False))
                                     for index, row in enumerate(rows, 1)])
                captured.append({"kind": kind, "rows": len(rows), "sha256": digest})
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    return captured


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(trading_logs_dir()))
    args = parser.parse_args()
    print(json.dumps(capture(args.source, data_home()), indent=2))


if __name__ == "__main__":
    main()
