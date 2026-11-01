from pathlib import Path

import pytest

from risklab.config import ConfigError, load_data_config, resolve_config_dir

pytestmark = pytest.mark.unit

VALID = """
raw_dir: data/raw
parquet_dir: /abs/parquet
manifest_path: data/manifest.json
tables:
  a.csv: a
duckdb:
  memory_limit: 2GB
  temp_directory: data/tmp
  threads: 2
"""


def write_cfg(root: Path, text: str) -> Path:
    cfg_dir = root / "configs"
    cfg_dir.mkdir()
    (cfg_dir / "data.yaml").write_text(text)
    return cfg_dir


def test_loads_and_resolves_relative_paths(tmp_path: Path) -> None:
    cfg = load_data_config(write_cfg(tmp_path, VALID))
    root = tmp_path.resolve()
    assert cfg.raw_dir == root / "data/raw"
    assert cfg.parquet_dir == Path("/abs/parquet")
    assert cfg.manifest_path == root / "data/manifest.json"
    assert cfg.duckdb.temp_directory == root / "data/tmp"
    assert cfg.duckdb.threads == 2
    assert cfg.duckdb.preserve_insertion_order is False


def test_env_var_selects_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_dir = write_cfg(tmp_path, VALID)
    monkeypatch.setenv("RISKLAB_CONFIG_DIR", str(cfg_dir))
    assert resolve_config_dir() == cfg_dir.resolve()
    assert load_data_config().raw_dir == tmp_path.resolve() / "data/raw"


def test_explicit_arg_beats_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RISKLAB_CONFIG_DIR", "/nonexistent")
    assert resolve_config_dir(tmp_path) == tmp_path.resolve()


def test_unknown_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="bogus"):
        load_data_config(write_cfg(tmp_path, VALID + "bogus: 1\n"))


def test_missing_field_has_clear_message(tmp_path: Path) -> None:
    text = VALID.replace("raw_dir: data/raw\n", "")
    with pytest.raises(ConfigError, match="raw_dir"):
        load_data_config(write_cfg(tmp_path, text))


def test_bad_threads_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="threads"):
        load_data_config(write_cfg(tmp_path, VALID.replace("threads: 2", "threads: 0")))


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_data_config(tmp_path / "configs")


def test_non_mapping_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="mapping"):
        load_data_config(write_cfg(tmp_path, "- a\n- b\n"))


def test_repo_config_is_valid() -> None:
    root = Path(__file__).resolve().parents[2]
    cfg = load_data_config(root / "configs")
    assert cfg.duckdb.memory_limit == "4GB"
    assert "application_train.csv" in cfg.tables
