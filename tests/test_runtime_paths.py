from pathlib import Path

from data_providers import runtime


def test_checkout_defaults_to_its_own_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_PROVIDERS_HOME", raising=False)
    monkeypatch.setenv("DATA_PROVIDERS_CONFIG", str(tmp_path / "absent.toml"))
    assert runtime.data_home() == Path(runtime.__file__).resolve().parents[1] / "runtime"


def test_explicit_home_and_relative_configuration(tmp_path, monkeypatch):
    config = tmp_path / "storage.toml"
    config.write_text('[storage]\nhome="warehouse"\n')
    monkeypatch.delenv("DATA_PROVIDERS_HOME", raising=False)
    monkeypatch.setenv("DATA_PROVIDERS_CONFIG", str(config))
    assert runtime.data_home() == tmp_path / "warehouse"
    monkeypatch.setenv("DATA_PROVIDERS_HOME", str(tmp_path / "override"))
    assert runtime.data_home() == tmp_path / "override"


def test_trading_logs_are_an_external_input_not_storage_sibling(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_PROVIDERS_HOME", str(tmp_path / "warehouse"))
    monkeypatch.setenv("TRADING_LOGS_DIR", str(tmp_path / "source"))
    assert runtime.trading_logs_dir() == tmp_path / "source"


def test_container_input_mount_does_not_depend_on_data_home_depth(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_PROVIDERS_HOME", str(tmp_path / "nested/warehouse"))
    monkeypatch.delenv("TRADING_LOGS_DIR", raising=False)
    monkeypatch.setattr(Path, "is_dir", lambda path: path == Path("/root/trading_logs"))
    assert runtime.trading_logs_dir() == Path("/root/trading_logs")
