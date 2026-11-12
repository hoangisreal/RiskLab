"""Local browser-test backend with disposable PostgreSQL and synthetic test accounts only.

Never use this entrypoint for deployment: it seeds test-only credentials and
deliberately isolates its database instead of consuming an application DB URL.
It also fits a small synthetic model and runs the real worker loop so browser
tests exercise import -> scoring -> review -> decision, never a mocked score.
"""

from __future__ import annotations

import hashlib
import pickle
import shutil
import signal
import threading
import time
from pathlib import Path

import uvicorn
import yaml
from tests.business.conftest import disposable_database, disposable_postgres
from tests.conftest import CONFIG_DIR, REPO_ROOT, write_config_dir
from tests.fixtures.synthetic import generate
from typer.testing import CliRunner

from risklab.business.catalog import DemoCatalog, PinnedRelease
from risklab.business.db import session_factory
from risklab.business.identity import HASHER
from risklab.business.main import create_app
from risklab.business.models import User
from risklab.business.scoring import PinnedScoringModel
from risklab.business.settings import Settings
from risklab.business.storage import LocalStorage
from risklab.business.worker import WorkerRuntime, run_once
from risklab.cli import app as cli_app
from risklab.config import load_data_config, load_partition_config, load_raw_schema
from risklab.features.application import load_application_frame
from risklab.models.pipelines import load_lgbm_params, make_lgbm_pipeline
from risklab.models.train import fit_pipeline


def build_scorer(root: Path) -> tuple[PinnedScoringModel, DemoCatalog, PinnedRelease, LocalStorage]:
    """Fit the proven synthetic recipe (500 Train rows) solely for browser tests."""
    from risklab.config import load_data_config as _load

    repo_cfg = _load(CONFIG_DIR)
    duck = repo_cfg.duckdb.model_copy(
        update={"temp_directory": root / "duck_tmp", "memory_limit": "1GB"}
    )
    cfg = repo_cfg.model_copy(
        update={
            "raw_dir": root / "raw",
            "parquet_dir": root / "parquet",
            "manifest_path": root / "manifest.json",
            "reports_dir": root / "reports",
            "duckdb": duck,
        }
    )
    schema = load_raw_schema(CONFIG_DIR)
    generate(cfg.raw_dir, schema, {t: f for f, t in cfg.tables.items()}, n_applicants=3000, seed=5)
    cfg_dir = write_config_dir(root, cfg, schema)
    for name in (
        "cleaning.yaml",
        "features_application.yaml",
        "features.yaml",
        "model_lr.yaml",
        "model_lgbm.yaml",
        "calibration.yaml",
        "cost.yaml",
        "monitoring.yaml",
        "serving_validation.yaml",
        "verification.yaml",
        "final_eval.yaml",
        "registry.yaml",
        "historical.yaml",
        "api.yaml",
        "application_validation.yaml",
        "application_domains.yaml",
    ):
        shutil.copy(CONFIG_DIR / name, cfg_dir / name)
    sql_link = root / "sql"
    if not sql_link.exists():
        sql_link.symlink_to(REPO_ROOT / "sql")
    (cfg_dir / "partition.yaml").write_text(
        yaml.safe_dump(
            {
                "seed": 11,
                "shares": {
                    "train": 0.55,
                    "calibration": 0.10,
                    "selection": 0.10,
                    "test": 0.15,
                    "monitoring": 0.10,
                },
                "source_table": "application_train",
                "id_column": "SK_ID_CURR",
                "stratify_column": "TARGET",
                "output_dir": "partitions",
            }
        )
    )
    runner = CliRunner()
    for step in ("ingest", "partition"):
        result = runner.invoke(cli_app, ["--config-dir", str(cfg_dir), step])
        assert result.exit_code == 0, result.output
    data_cfg = load_data_config(cfg_dir)
    partition_cfg = load_partition_config(cfg_dir)
    X, y = load_application_frame(
        "train", "fit", data_cfg=data_cfg, partition_cfg=partition_cfg, config_dir=cfg_dir
    )
    pipe = fit_pipeline(
        make_lgbm_pipeline(load_lgbm_params(cfg_dir), config_dir=str(cfg_dir)),
        X.head(500),
        y.head(500),
    )
    model_path = root / "model.pkl"
    payload = pickle.dumps(pipe)
    model_path.write_bytes(payload)
    release = PinnedRelease(
        model_version="synthetic-browser-v1",
        model_sha256=hashlib.sha256(payload).hexdigest(),
        model_run_id="synthetic-browser-run",
        lock_id="synthetic-browser-lock",
        feature_contract_version="1.1",
        feature_groups=["bureau", "previous", "installments"],
        threshold=0.1,
        policy_version="synthetic-policy-v1",
        protocol_hashes={"synthetic": "0" * 64},
        feature_columns=list(pipe.feature_names_in_),
    )
    scorer = PinnedScoringModel(
        model_path=model_path, release=release, config_dir=cfg_dir, root=root
    )
    catalog_data = (REPO_ROOT / "tests/fixtures/business_v1/catalog.json").read_bytes()
    catalog = DemoCatalog(
        catalog_data, expected_sha256=hashlib.sha256(catalog_data).hexdigest(), config_dir=cfg_dir
    )
    return scorer, catalog, release, LocalStorage(root / "objects")


def main() -> None:
    import os

    backend_port = int(os.environ.get("RISKLAB_BROWSER_BACKEND_PORT", "18000"))
    web_port = os.environ.get("RISKLAB_BROWSER_WEB_PORT", "13000")

    def stop(_signum: int, _frame: object) -> None:
        raise SystemExit(0)

    # Uvicorn restores this handler on shutdown, so owned test containers are cleaned up.
    signal.signal(signal.SIGTERM, stop)
    with disposable_postgres() as control, disposable_database(control) as database:
        with session_factory(database.runtime).begin() as session:
            for role in ("ADMIN", "MANAGER", "ANALYST"):
                session.add(
                    User(
                        username=role.lower() + ".browser",
                        role=role,
                        password_hash=HASHER.hash("synthetic-browser-password"),
                    )
                )
            # Project-scoped accounts keep the shared login rate limit (10/5min
            # per IP+username) from coupling desktop and mobile workflow runs.
            for project in ("desktop", "mobile"):
                for role in ("ADMIN", "MANAGER", "ANALYST"):
                    session.add(
                        User(
                            username=f"{role.lower()}.flow{project}",
                            role=role,
                            password_hash=HASHER.hash("synthetic-browser-password"),
                        )
                    )
        import tempfile

        with tempfile.TemporaryDirectory(prefix="risklab-browser-") as temporary:
            root = Path(temporary)
            scorer, catalog, release, storage = build_scorer(root)
            runtime = WorkerRuntime(
                storage=storage, config_dir=scorer.config_dir, root=root, scorer=scorer
            )

            def work() -> None:
                while True:
                    try:
                        if not run_once(database.runtime, runtime, worker_id="browser-worker"):
                            time.sleep(0.5)
                    except Exception:
                        time.sleep(0.5)

            threading.Thread(target=work, daemon=True).start()
            app = create_app(
                settings=Settings(environment="test", origin=f"http://127.0.0.1:{web_port}"),
                engine=database.runtime,
                catalog=catalog,
                release=release,
                storage=storage,
                comparison={
                    "report_version": "synthetic-browser-comparison-v1",
                    "data_manifest_hash": "a" * 64,
                    "partition_hash": "b" * 64,
                    "models": [],
                },
            )
            uvicorn.run(app, host="127.0.0.1", port=backend_port, access_log=False)


if __name__ == "__main__":
    main()
