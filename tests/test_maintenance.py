from types import SimpleNamespace
from unittest.mock import patch

from data_providers.maintenance import Step, execute, plan


def test_all_scopes_include_trading_logs_and_fill_tapes():
    for scope in ["all", "us", "cn", "trades"]:
        steps = plan(scope, year=2027)
        assert steps[0].module.endswith("scan_inputs")
        assert any(s.module.endswith("load_journal") for s in steps)
        assert any(s.args == ("--mode", "fills") for s in steps)
        assert all(s.module.startswith("data_providers.") for s in steps)
    cn = plan("cn", year=2027)
    assert any(s.args == ("--years", "2026", "2027") for s in cn)
    solver = next(s for s in cn if s.module.endswith("build_iv_history_cn_solver"))
    assert solver.args[1] == "DCE,CFFEX,GFEX"


def test_failures_block_dependencies_but_trades_still_run(tmp_path):
    steps = [Step("us", "fail", "first"), Step("us", "skip", "second"),
             Step("trades", "log", "third")]
    with patch("data_providers.maintenance.subprocess.run",
               side_effect=[SimpleNamespace(returncode=2), SimpleNamespace(returncode=0)]) as run:
        result = execute(steps, home=tmp_path)
    assert not result["ok"]
    assert [s["status"] for s in result["steps"]] == ["failed", "blocked", "ok"]
    assert run.call_count == 2
    assert (tmp_path / "var/maintenance" / result["run_id"] / "result.json").exists()


def test_unavailable_trade_source_blocks_loaders_and_brokers(tmp_path):
    steps = [Step("trade_inputs", "scan", "scan"), Step("receipts", "archive", "archive"),
             Step("brokers", "fetch", "fetch"), Step("trades", "load", "load"),
             Step("verify", "audit", "audit")]
    with patch("data_providers.maintenance.subprocess.run",
               side_effect=[SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)]) as run:
        result = execute(steps, home=tmp_path)
    assert not result["ok"]
    assert [s["status"] for s in result["steps"]] == ["failed", "blocked", "blocked", "blocked", "ok"]
    assert run.call_count == 2


def test_default_plans_never_force_history_or_invent_underlying_prices():
    for scope in ("all", "us", "cn", "derivatives"):
        steps = plan(scope, year=2026)
        assert all("--force" not in step.args for step in steps)
        assert all("--synthetic" not in step.args for step in steps)
        assert any(step.module.endswith("fetch_tws_ibkr") for step in steps)
    # Explicit historical settlements remain a distinct user-selected mode.
    requested = plan("cn", year=2026, with_settle=True)
    assert any("--force" in step.args and step.module.endswith("fetch_cn_products")
               for step in requested)


def test_explicit_writer_wait_serializes_without_bypassing_owner(tmp_path):
    import fcntl
    from concurrent.futures import ThreadPoolExecutor
    import pytest

    folder = tmp_path / 'var' / 'maintenance'
    folder.mkdir(parents=True)
    with (folder / 'writer.lock').open('a') as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        with pytest.raises(RuntimeError, match='Another maintenance run is active'):
            execute([], home=tmp_path)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(execute, [], home=tmp_path, wait_for_writer=True)
            try:
                with pytest.raises(TimeoutError):
                    pending.result(timeout=0.1)
            finally:
                fcntl.flock(owner, fcntl.LOCK_UN)
            assert pending.result(timeout=5)['ok']
