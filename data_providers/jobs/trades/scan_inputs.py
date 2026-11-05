"""Rescan authoritative trading inputs on every maintenance invocation."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from data_providers.jobs.trades.archive_exports import export_kind
from data_providers.runtime import data_home, trading_logs_dir


def scan(source, home):
    source = Path(source).resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Trading input folder unavailable: {source}")
    folder = Path(home) / "var/maintenance/trading-inputs"
    latest = folder / "latest.json"
    previous = json.loads(latest.read_text()) if latest.exists() else {}
    old = {x["path"]: x for x in previous.get("files", [])} if previous.get("source") == str(source) else {}
    files = []
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part.startswith((".", "~$")) for part in relative.parts) or not path.is_file():
            continue
        # Do not follow a link out of the mounted input folder.
        if not path.resolve().is_relative_to(source):
            continue
        before = path.stat()
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise RuntimeError(f"Trading input changed during scan: {relative}")
        files.append({"path": str(relative), "kind": export_kind(path),
                      "sha256": digest, "bytes": after.st_size, "modified_ns": after.st_mtime_ns})
    current = {x["path"]: x for x in files}
    newest = {}
    for row in files:
        key = row["kind"]
        if key not in newest or (row["modified_ns"], row["path"]) > (
                newest[key]["modified_ns"], newest[key]["path"]):
            newest[key] = row
    report = {"observed_utc": datetime.now(UTC).isoformat(), "source": str(source),
              "files": files, "newest_by_type": newest,
              "added": sorted(current.keys() - old.keys()),
              "changed": sorted(k for k in current.keys() & old.keys()
                                if current[k]["sha256"] != old[k]["sha256"]),
              "removed": sorted(old.keys() - current.keys()),
              "policy": "Fresh source inventory; receipts are not executions; unsupported files are not auto-loaded"}
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = folder / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8] + ".json")
    report["snapshot"] = str(snapshot)
    payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    snapshot.write_text(payload)
    pending = latest.with_suffix(".tmp")
    pending.write_text(payload)
    pending.replace(latest)
    return report


def main():
    report = scan(trading_logs_dir(), data_home())
    print(json.dumps({key: report[key] for key in ("source", "snapshot", "added", "changed", "removed")}))


if __name__ == "__main__":
    main()
