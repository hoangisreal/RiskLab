from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from risklab.config import DataConfig, RawSchema, load_data_config, load_raw_schema

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs"


@pytest.fixture(scope="session")
def repo_data_config() -> DataConfig:
    return load_data_config(CONFIG_DIR)


@pytest.fixture(scope="session")
def raw_schema(repo_data_config: DataConfig) -> RawSchema:
    return load_raw_schema(CONFIG_DIR, data_cfg=repo_data_config)


@pytest.fixture
def make_config(tmp_path: Path, repo_data_config: DataConfig) -> Callable[..., DataConfig]:
    """Build a DataConfig whose raw, parquet, report and spill paths live under ``tmp_path``."""

    def _make(tables: dict[str, str] | None = None) -> DataConfig:
        duck = repo_data_config.duckdb.model_copy(
            update={"temp_directory": tmp_path / "duck_tmp", "memory_limit": "1GB"}
        )
        return repo_data_config.model_copy(
            update={
                "raw_dir": tmp_path / "raw",
                "parquet_dir": tmp_path / "parquet",
                "manifest_path": tmp_path / "manifest.json",
                "reports_dir": tmp_path / "reports",
                "tables": tables or repo_data_config.tables,
                "duckdb": duck,
            }
        )

    return _make


def write_config_dir(root: Path, cfg: DataConfig, schema: RawSchema) -> Path:
    """Write ``root/configs/{data,raw_schema}.yaml`` for CLI tests and return the directory."""
    cfg_dir = root / "configs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "data.yaml").write_text(
        yaml.safe_dump(cfg.model_dump(mode="json")), encoding="utf-8"
    )
    (cfg_dir / "raw_schema.yaml").write_text(
        yaml.safe_dump(schema.model_dump(mode="json")), encoding="utf-8"
    )
    return cfg_dir


@pytest.fixture(scope="session")
def mlflow_server(tmp_path_factory):
    """Isolated HTTP tracking/registry service; clients never use its SQLite backend directly."""
    import os
    import socket
    import subprocess
    import sys
    import time
    import urllib.error
    import urllib.request

    root = tmp_path_factory.mktemp("mlflow-server")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    uri = f"http://127.0.0.1:{port}"
    env = {**os.environ, "MLFLOW_DISABLE_AGENT_HINT": "1"}
    env.pop("MLFLOW_TRACKING_URI", None)
    with open(root / "server.log", "w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mlflow",
                "server",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
                "--backend-store-uri",
                f"sqlite:///{root / 'tracking.db'}",
                "--artifacts-destination",
                str(root / "artifacts"),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=root,
        )
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(uri + "/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError):
                    if process.poll() is not None:
                        pytest.fail((root / "server.log").read_text())
                    time.sleep(0.2)
            else:
                pytest.fail("MLflow test server did not become ready")
            yield uri
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
