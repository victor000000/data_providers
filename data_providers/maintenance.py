"""Explicit maintenance plans; execution records failures and never reports partial success as clean."""
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .runtime import data_home


@dataclass
class Step:
    group: str
    name: str
    module: str
    args: tuple = ()


def plan(scope="all", *, year=None, with_bars=False, with_settle=False, workers=1, futures_full=False):
    year = year or date.today().year
    steps = []

    def add(group, name, module, *args):
        steps.append(Step(group, name, "data_providers." + module, tuple(map(str, args))))

    add("trade_inputs", "Rescan mounted trading-log folder and detect changed files", "jobs.trades.scan_inputs")
    if scope in {"all", "us"}:
        add("volatility_prices", "Seed missing volatility ETP price histories", "jobs.history.ensure_volatility_prices")
        add("us", "Refresh US underlying prices", "warehouse.35_refresh_tail")
        add("us", "Rebuild US price warehouse", "warehouse.60_build_database")
        add("us", "Fill US ATM ±7 IV history", "jobs.history.download_atm_iv",
            "--tickers-file", "plans/universe_download.txt", "--strikes", 7, "--gapfill")
        if with_bars:
            add("us", "Refresh US option bars", "jobs.history.download_option_bars",
                "--tickers-file", "plans/universe_download.txt", "--bulk")
        add("us", "Merge verified US staging", "data.staging.merge_staging")
    if scope in {"all", "us"}:
        add("etf", "Build ETF source universe", "jobs.history.build_etf_universe")
        add("etf", "Refresh ETF ATM IV history", "jobs.history.download_atm_iv",
            "--tickers-file", "plans/universe_etf_options.txt", "--months", 24, "--strikes", 7,
            "--gapfill", "--staging", "etf_iv")
        if with_bars:
            add("etf", "Refresh ETF option bars", "jobs.history.download_option_bars",
                "--tickers-file", "plans/universe_etf_options.txt", "--bulk", "--gapfill", "--staging", "etf_bars")
        add("etf", "Merge verified ETF staging", "data.staging.merge_staging")
    if scope in {"all", "us", "derivatives"}:
        add("futures", "Refresh US futures contracts, chains and session bars", "jobs.history.maintain_us_futures",
            *(["--full"] if futures_full else []))
        add("vix", "Refresh CFE VIX futures and futures options", "jobs.history.maintain_vix")
        add("cfe_catalog", "Discover and backfill other CFE futures", "jobs.history.maintain_catalogs", "--market", "cfe")
        add("us_catalog", "Discover and backfill provider-wide US futures", "jobs.history.maintain_catalogs", "--market", "us")
    if scope in {"all", "cn", "derivatives"}:
        add("cn", "Seed CN products", "data.schema.cn", "--init")
        add("cn", "Refresh CZCE IV", "jobs.history.build_iv_history_cn_bulk", "--years", year - 1, year)
        add("cn", "Refresh venue IV", "jobs.history.build_iv_history_cn_venue", "--venues", "SHFE,INE")
        add("cn", "Refresh solved IV and bars", "jobs.history.build_iv_history_cn_solver",
            "--venues", "DCE,CFFEX,GFEX", "--bars", "--workers", workers)
        for product in "MA TA SR CF RM OI FG SA UR PK ZC PX SH".split():
            add("cn", f"Refresh CZCE {product} bars", "jobs.data_ops.download_cn_option_bars",
                "--czce-product", product, "--czce-years", year - 1, year,
                "--ine-days", 0, "--cffex-contracts")
        add("cn", "Refresh SHFE/INE bars", "jobs.data_ops.fetch_cn_products", "--venues", "SHFE,INE", "--days", 60)
        add("cn", "Refresh CN futures", "jobs.history.build_cn_futures")
        if with_settle:
            add("cn", "Refresh historical settlements", "jobs.data_ops.fetch_cn_products",
                "--venues", "SHFE,INE", "--days", 640, "--force", "--workers", workers)
        add("cn", "Capture all listed CN option strikes and futures contract months", "jobs.history.maintain_cn_futures",
            *(["--history-months", 24] if with_settle else []))
        add("cn", "Backfill all listed CN option strikes", "jobs.history.maintain_cn_option_history")
        add("cn_catalog", "Discover and backfill provider-wide CN derivatives", "jobs.history.maintain_catalogs", "--market", "cn")
    # Every market maintenance run includes trading records and fill-minute capture.
    add("receipts", "Archive newest trading inputs with provenance", "jobs.trades.archive_exports")
    add("receipts", "Capture newest KQ/YC exports by type", "jobs.trades.capture_exports")
    add("brokers", "Refresh available Schwab exports", "jobs.trades.refresh_schwab")
    for name, module in [("Import journal", "load_journal"), ("Import CN settlements", "load_cn_settlements"),
                         ("Import classic QSQH orders", "load_qsqh_orders")]:
        add("trades", name, "jobs.trades." + module)
    add("trades", "Import header-identified CN broker snapshots", "jobs.trades.import_cn_tracker")
    add("trades", "Capture US fill minute tapes", "jobs.data_ops.download_minute_bars", "--mode", "fills")
    add("trades", "Merge retained US fill minute tapes", "jobs.data_ops.merge_fill_minutes")
    add("brokers", "Refresh Gateway executions and positions", "jobs.trades.fetch_tws_ibkr")
    add("verify", "Verify duplicate keys", "jobs.audit.check_duplicates")
    return steps


def execute(steps, *, home=None, timeout=21600, progress=None, wait_for_writer=False):
    import fcntl
    home = Path(home) if home else data_home()
    folder = home / "var" / "maintenance"
    folder.mkdir(parents=True, exist_ok=True)
    # Kernel lock releases after crashes; the file is never removed while another process can open it.
    with (folder / "writer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait_for_writer else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise RuntimeError("Another maintenance run is active") from exc
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        run_dir = folder / run_id
        run_dir.mkdir()
        results, failed_groups = [], set()
        env = dict(os.environ, DATA_PROVIDERS_HOME=str(home.resolve()), TZ="UTC",
                   DATA_PROVIDERS_MAINTENANCE_LOCKED=run_id)
        for index, step in enumerate(steps):
            if progress:
                progress(dict(run_id=run_id, steps=list(results), total=len(steps), current=step.name))
            record = asdict(step)
            if "trade_inputs" in failed_groups and step.group in {"receipts", "brokers", "trades"}:
                record.update(status="blocked", reason="Authoritative trading input scan failed")
            elif step.group in failed_groups:
                record.update(status="blocked", reason="A prerequisite in this group failed")
            else:
                logfile = run_dir / f"{index:02}-{step.module.rsplit('.', 1)[-1]}.log"
                start = time.monotonic()
                print(f"{step.group}: {step.name}", flush=True)
                with logfile.open("w") as output:
                    try:
                        proc = subprocess.run([sys.executable, "-m", step.module, *step.args], cwd=home,
                                              env=env, stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
                        record.update(status="ok" if proc.returncode == 0 else "failed", rc=proc.returncode)
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        record.update(status="failed", error=type(exc).__name__)
                record.update(log=str(logfile), seconds=round(time.monotonic() - start, 2))
                if record["status"] != "ok":
                    failed_groups.add(step.group)
            results.append(record)
            if progress:
                progress(dict(run_id=run_id, steps=list(results), total=len(steps), current=None))
            temporary = run_dir / "result.tmp"
            temporary.write_text(json.dumps({"run_id": run_id, "steps": results}, indent=2) + "\n")
            temporary.replace(run_dir / "result.json")
        return {"run_id": run_id, "ok": not failed_groups, "steps": results}


def publish_result(result, home):
    """Run only after execute releases its writer lock; persist delivery failure too."""
    from .lake import publish
    try:
        manifest = publish(home, private=True)
        result["publication"] = {"status": "ok", "release": manifest["release"]}
    except Exception as exc:  # Publication failure must not be mistaken for maintenance success.
        result["ok"] = False
        result["publication"] = {"status": "failed", "error": str(exc)}
    folder = Path(home) / "var" / "maintenance" / result["run_id"]
    temporary = folder / "summary.tmp"
    temporary.write_text(json.dumps(result, indent=2) + "\n")
    temporary.replace(folder / "summary.json")
    return result
