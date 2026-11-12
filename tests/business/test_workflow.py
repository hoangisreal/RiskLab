"""End-to-end business workflow over real PostgreSQL, storage and a synthetic model.

Covers B03 import -> worker scoring -> persisted prediction, B04 review and
decision guards, queue fencing and B05 report scoping. The fitted pipeline is
a synthetic test artifact only, never a production bundle identity.
"""

from __future__ import annotations

import hashlib
import pickle
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.conftest import CONFIG_DIR, REPO_ROOT, write_config_dir
from tests.fixtures.synthetic import generate
from typer.testing import CliRunner

from risklab.business.catalog import DemoCatalog, PinnedRelease
from risklab.business.db import session_factory
from risklab.business.identity import HASHER
from risklab.business.jobs import claim_job, complete_job, fail_job
from risklab.business.main import create_app
from risklab.business.models import (
    Application,
    AuditEvent,
    Decision,
    Job,
    Prediction,
    User,
    now_utc,
)
from risklab.business.scoring import PinnedScoringModel
from risklab.business.settings import Settings
from risklab.business.storage import LocalStorage
from risklab.business.worker import WorkerRuntime, run_once
from risklab.cli import app as cli_app
from risklab.config import load_data_config, load_partition_config
from risklab.features.application import load_application_frame
from risklab.models.pipelines import load_lgbm_params, make_lgbm_pipeline
from risklab.models.train import fit_pipeline

pytestmark = pytest.mark.integration
PASSWORD = "synthetic-test-password-only"
FIXTURES = REPO_ROOT / "tests/fixtures/business_v1"


@pytest.fixture(scope="module")
def model_env(tmp_path_factory, repo_data_config, raw_schema):
    root = tmp_path_factory.mktemp("workflow-model")
    duck = repo_data_config.duckdb.model_copy(
        update={"temp_directory": root / "duck_tmp", "memory_limit": "1GB"}
    )
    cfg = repo_data_config.model_copy(
        update={
            "raw_dir": root / "raw",
            "parquet_dir": root / "parquet",
            "manifest_path": root / "manifest.json",
            "reports_dir": root / "reports",
            "duckdb": duck,
        }
    )
    generate(
        cfg.raw_dir, raw_schema, {t: f for f, t in cfg.tables.items()}, n_applicants=3000, seed=5
    )
    cfg_dir = write_config_dir(root, cfg, raw_schema)
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
        model_version="synthetic-workflow-v1",
        model_sha256=hashlib.sha256(payload).hexdigest(),
        model_run_id="synthetic-workflow-run",
        lock_id="synthetic-workflow-lock",
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
    return cfg_dir, root, release, scorer


@pytest.fixture
def workflow_env(database, model_env, tmp_path):
    cfg_dir, root, release, scorer = model_env
    factory = session_factory(database.runtime)
    users = {}
    with factory.begin() as session:
        for name, role in [
            ("manager.demo", "MANAGER"),
            ("manager.two", "MANAGER"),
            ("analyst.demo", "ANALYST"),
            ("analyst.two", "ANALYST"),
            ("admin.demo", "ADMIN"),
        ]:
            user = User(username=name, role=role, password_hash=HASHER.hash(PASSWORD))
            session.add(user)
            session.flush()
            users[name] = user.id
    data = (FIXTURES / "catalog.json").read_bytes()
    catalog = DemoCatalog(
        data, expected_sha256=hashlib.sha256(data).hexdigest(), config_dir=cfg_dir
    )
    storage = LocalStorage(tmp_path / "objects")
    app = create_app(
        settings=Settings(environment="test", origin="http://testserver"),
        engine=database.runtime,
        catalog=catalog,
        release=release,
        storage=storage,
        comparison={
            "report_version": "synthetic-comparison-v1",
            "data_manifest_hash": "a" * 64,
            "partition_hash": "b" * 64,
            "models": [],
        },
    )
    runtime = WorkerRuntime(storage=storage, config_dir=cfg_dir, root=root, scorer=scorer)
    return app, factory, users, runtime, database.runtime


def signed_in(app, username):
    client = TestClient(app)
    client.headers["origin"] = "http://testserver"
    csrf = client.get("/api/v1/auth/csrf").json()["data"]["csrf_token"]
    client.headers["x-csrf-token"] = csrf
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["data"]["csrf_token"]
    return client


def import_and_score(env, *, key="workflow-import-001"):
    app, factory, users, runtime, engine = env
    manager = signed_in(app, "manager.demo")
    csv_data = (FIXTURES / "import-valid.csv").read_bytes()
    response = manager.post(
        "/api/v1/imports",
        headers={"Idempotency-Key": key},
        data={"import_contract_version": "demo-import/1", "catalog_version": "synthetic-b00-v1"},
        files={"file": ("demo.csv", csv_data, "text/csv")},
    )
    assert response.status_code == 202, response.text
    # Import job, then the two READY scoring jobs; INCOMPLETE never queues.
    for _ in range(3):
        assert run_once(engine, runtime, worker_id="worker-test") is True
    assert run_once(engine, runtime, worker_id="worker-test") is False
    return manager, response.json()["data"]


def scored_application(env, manager, snapshot_id="demo-history"):
    listing = manager.get("/api/v1/applications?page_size=100")
    assert listing.status_code == 200, listing.text
    factory = env[1]
    with factory() as session:
        rows = {row.snapshot_id: row for row in session.scalars(select(Application))}
    return rows[snapshot_id]


def assign(manager, application, assignee_id, *, reason="Assign for review"):
    detail = manager.get(f"/api/v1/applications/{application.id}").json()["data"]
    response = manager.put(
        f"/api/v1/applications/{application.id}/assignment",
        json={
            "expected_revision": detail["revision"],
            "assignee_id": str(assignee_id),
            "reason": reason,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def submit_as(analyst, application_id):
    detail = analyst.get(f"/api/v1/applications/{application_id}").json()["data"]
    started = analyst.post(
        f"/api/v1/applications/{application_id}/reviews",
        json={"expected_revision": detail["revision"]},
    )
    assert started.status_code == 201, started.text
    review = started.json()["data"]["review"]
    revision = started.json()["data"]["application_revision"]
    patched = analyst.patch(
        f"/api/v1/applications/{application_id}/reviews/{review['id']}",
        json={
            "expected_revision": revision,
            "notes": "Checked data and limits.",
            "proposal": "DECLINE",
        },
    )
    assert patched.status_code == 200, patched.text
    submitted = analyst.post(
        f"/api/v1/applications/{application_id}/reviews/{review['id']}/submit",
        json={"expected_revision": patched.json()["data"]["application_revision"]},
    )
    assert submitted.status_code == 200, submitted.text
    return submitted.json()["data"]


def test_import_worker_scoring_and_prediction_provenance(workflow_env):
    app, factory, users, runtime, engine = workflow_env
    manager, batch = import_and_score(workflow_env)
    job = manager.get(f"/api/v1/imports/{batch['id']}").json()["data"]
    assert job["state"] == "SUCCEEDED"
    assert (job["created_count"], job["ready_count"], job["incomplete_count"]) == (3, 2, 1)
    with factory() as session:
        rows = {row.snapshot_id: row for row in session.scalars(select(Application))}
        assert rows["demo-missing-history"].scoring_state == "NOT_REQUESTED"
        assert rows["demo-missing-history"].feature_availability == "INCOMPLETE"
        predictions = list(session.scalars(select(Prediction)))
        assert len(predictions) == 2
        for prediction in predictions:
            assert prediction.model_version == "synthetic-workflow-v1"
            assert prediction.policy_version == "synthetic-policy-v1"
            assert prediction.explanation["space"] == "log_odds_uncalibrated"
            assert 0 <= prediction.probability <= 1
        jobs = list(session.scalars(select(Job)))
        assert sorted(row.kind for row in jobs) == ["IMPORT", "SCORING", "SCORING"]
        assert all(row.state == "SUCCEEDED" for row in jobs)
    history_app = rows["demo-history"]
    detail = manager.get(f"/api/v1/applications/{history_app.id}").json()["data"]
    assert detail["scoring_state"] == "SUCCEEDED" and detail["prediction_id"]
    prediction = manager.get(f"/api/v1/applications/{history_app.id}/prediction").json()["data"]
    assert len(prediction["model_sha256"]) == 64
    snapshot = manager.get(f"/api/v1/applications/{history_app.id}/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["data"]["history"]["bureau"]["status"] == "provided"
    # Replay returns the original 202 intent without duplicating work.
    csv_data = (FIXTURES / "import-valid.csv").read_bytes()
    replay = manager.post(
        "/api/v1/imports",
        headers={"Idempotency-Key": "workflow-import-001"},
        data={"import_contract_version": "demo-import/1", "catalog_version": "synthetic-b00-v1"},
        files={"file": ("demo.csv", csv_data, "text/csv")},
    )
    assert replay.status_code == 202 and replay.json()["data"]["id"] == batch["id"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Application)) == 3


def test_full_review_return_resubmit_decide_and_terminal(workflow_env):
    app, factory, users, runtime, engine = workflow_env
    manager, _ = import_and_score(workflow_env)
    target = scored_application(workflow_env, manager)
    assign(manager, target, users["analyst.demo"])
    analyst = signed_in(app, "analyst.demo")
    submitted = submit_as(analyst, target.id)
    # Analyst and Admin can never decide, even by direct API call.
    for client in (analyst, signed_in(app, "admin.demo")):
        response = client.post(
            f"/api/v1/applications/{target.id}/decisions",
            headers={"Idempotency-Key": "forbidden-decide-1"},
            json={
                "expected_revision": submitted["application_revision"],
                "review_id": submitted["review"]["id"],
                "outcome": "APPROVED_SIMULATED",
                "reason": "Forbidden attempt",
            },
        )
        assert response.status_code == 403, response.text
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Decision)) == 0
    # Return creates a fresh draft; the old submission stays immutable history.
    returned = manager.post(
        f"/api/v1/applications/{target.id}/return",
        json={
            "expected_revision": submitted["application_revision"],
            "review_id": submitted["review"]["id"],
            "reason": "Needs more detail",
        },
    )
    assert returned.status_code == 200, returned.text
    assert returned.json()["data"]["review_state"] == "IN_REVIEW"
    draft = returned.json()["data"]["current_review"]
    assert draft["review_number"] == 2 and draft["submitted_at"] is None
    patched = analyst.patch(
        f"/api/v1/applications/{target.id}/reviews/{draft['id']}",
        json={
            "expected_revision": returned.json()["data"]["revision"],
            "notes": "Added detail after return.",
            "proposal": "APPROVE",
        },
    )
    assert patched.status_code == 200, patched.text
    resubmitted = analyst.post(
        f"/api/v1/applications/{target.id}/reviews/{draft['id']}/submit",
        json={"expected_revision": patched.json()["data"]["application_revision"]},
    )
    assert resubmitted.status_code == 200, resubmitted.text
    # Two Managers racing on the same revision produce exactly one decision.
    second = signed_in(app, "manager.two")
    payload = {
        "expected_revision": resubmitted.json()["data"]["application_revision"],
        "review_id": draft["id"],
        "outcome": "DECLINED_SIMULATED",
        "reason": "Simulated decision after review",
    }

    def decide(client, key):
        return client.post(
            f"/api/v1/applications/{target.id}/decisions",
            headers={"Idempotency-Key": key},
            json=payload,
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(
            pool.map(
                lambda pair: decide(*pair),
                [(manager, "race-decide-001"), (second, "race-decide-002")],
            )
        )
    assert codes == [201, 409], codes
    with factory() as session:
        decisions = list(session.scalars(select(Decision)))
        assert len(decisions) == 1
        assert decisions[0].review_id == UUID(draft["id"])
        assert session.get(Application, target.id).review_state == "DECIDED"
    # Idempotent replay returns the same decision; DECIDED is terminal.
    replay = manager.post(
        f"/api/v1/applications/{target.id}/decisions",
        headers={"Idempotency-Key": "race-decide-001"},
        json=payload,
    )
    replay2 = second.post(
        f"/api/v1/applications/{target.id}/decisions",
        headers={"Idempotency-Key": "race-decide-002"},
        json=payload,
    )
    assert sorted([replay.status_code, replay2.status_code]) == [201, 409]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Decision)) == 1


def test_separation_of_duties_after_role_change_and_cross_object_access(workflow_env):
    app, factory, users, runtime, engine = workflow_env
    manager, _ = import_and_score(workflow_env)
    target = scored_application(workflow_env, manager)
    assign(manager, target, users["analyst.demo"])
    analyst = signed_in(app, "analyst.demo")
    submitted = submit_as(analyst, target.id)
    # The reviewing Analyst is promoted; the new Manager role must not unlock deciding.
    with factory.begin() as session:
        session.get(User, users["analyst.demo"]).role = "MANAGER"
    promoted = signed_in(app, "analyst.demo")
    response = promoted.post(
        f"/api/v1/applications/{target.id}/decisions",
        headers={"Idempotency-Key": "promoted-decide-1"},
        json={
            "expected_revision": submitted["application_revision"],
            "review_id": submitted["review"]["id"],
            "outcome": "APPROVED_SIMULATED",
            "reason": "Must stay forbidden",
        },
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "SEPARATION_OF_DUTIES"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Decision)) == 0
    # Cross-object: the other Analyst cannot read detail, snapshot or prediction.
    other = signed_in(app, "analyst.two")
    for suffix in ("", "/snapshot", "/prediction", "/history"):
        assert other.get(f"/api/v1/applications/{target.id}{suffix}").status_code == 404
    assert signed_in(app, "admin.demo").get(f"/api/v1/applications/{target.id}").status_code == 403
    listing = other.get("/api/v1/applications").json()
    assert listing["pagination"]["total"] == 0


def test_queue_fencing_expired_lease_and_stale_completion(workflow_env):
    app, factory, users, runtime, engine = workflow_env
    manager, batch = import_and_score(workflow_env)
    target = scored_application(workflow_env, manager)
    # Directly exercise claim fencing on a fresh scoring-style job.
    from risklab.business.jobs import enqueue_job

    with factory.begin() as session:
        extra = Application(
            import_id=UUID(batch["id"]),
            external_reference="FENCING-001",
            catalog_version="synthetic-b00-v1",
            snapshot_id="demo-no-history",
            input_sha256=target.input_sha256,
            feature_contract_version="1.1",
            source_availability={},
            missing_sources=[],
            feature_availability="READY",
            scoring_state="QUEUED",
        )
        session.add(extra)
        session.flush()
        job = enqueue_job(session, kind="SCORING", application_id=extra.id)
        job_id = job.id
    with factory.begin() as session:
        claimed = claim_job(session, worker_id="worker-a")
        assert claimed is not None and claimed.id == job_id and claimed.attempt == 1
        claimed.lease_expires_at = now_utc() - timedelta(seconds=1)
    with factory.begin() as session:
        reclaimed = claim_job(session, worker_id="worker-b")
        assert reclaimed is not None and reclaimed.attempt == 2
        assert complete_job(session, job_id=job_id, attempt=1, worker_id="worker-a") is False
        assert complete_job(session, job_id=job_id, attempt=2, worker_id="worker-b") is True
    with factory.begin() as session:
        job = enqueue_job(
            session,
            kind="SCORING",
            application_id=extra.id,
        )
        retry_id = job.id
    with factory.begin() as session:
        claimed = claim_job(session, worker_id="worker-a")
        assert claimed is not None
        assert (
            fail_job(
                session,
                job_id=retry_id,
                attempt=claimed.attempt,
                worker_id="worker-a",
                error_code="MODEL_UNAVAILABLE",
                retryable=True,
            )
            == "QUEUED"
        )


def test_worker_scoring_matches_legacy_serving_path(workflow_env, model_env):
    """Batch-vs-worker parity: the adapter must not drift from shared serving."""
    import json as json_module

    from risklab.api.model_store import ModelStore
    from risklab.api.schemas import PredictRequest
    from risklab.api.service import predict as legacy_predict
    from risklab.features.registry import load_registry

    cfg_dir, root, release, scorer = model_env
    payload = json_module.loads((FIXTURES / "catalog.json").read_text())["snapshots"][
        "demo-history"
    ]
    store = ModelStore()
    store.swap(scorer.loaded_model)
    legacy = legacy_predict(
        PredictRequest.model_validate(
            {
                "contract_version": payload["contract_version"],
                "application": payload["application"],
                "history": payload["history"],
                "explain": True,
            }
        ),
        store=store,
        registry=load_registry(cfg_dir),
        root=root,
        config_dir=cfg_dir,
    )
    worker = scorer.score(payload)
    assert worker.probability == pytest.approx(legacy.probability, abs=1e-12)
    assert worker.explanation["raw_log_odds"] == pytest.approx(
        legacy.explanation.raw_log_odds, abs=1e-9
    )


def test_workload_reports_are_scoped(workflow_env):
    app, factory, users, runtime, engine = workflow_env
    manager, _ = import_and_score(workflow_env)
    target = scored_application(workflow_env, manager)
    assign(manager, target, users["analyst.demo"])
    report = manager.get("/api/v1/reports/workload").json()["data"]
    assert report["total"] == 3
    analyst = signed_in(app, "analyst.demo")
    scoped = analyst.get("/api/v1/reports/workload").json()["data"]
    assert scoped["total"] == 1
    assert signed_in(app, "admin.demo").get("/api/v1/reports/workload").status_code == 403
    current = analyst.get("/api/v1/models/current").json()["data"]
    assert current["model_version"] == "synthetic-workflow-v1"
    assert current["limitations"]
    comparison = analyst.get("/api/v1/models/comparison").json()["data"]
    assert comparison["report_version"] == "synthetic-comparison-v1"
    history = analyst.get(f"/api/v1/applications/{target.id}/history").json()
    assert history["pagination"]["total"] >= 1
    with factory() as session:
        actions = {row.action for row in session.scalars(select(AuditEvent))}
    assert "application.assigned" in actions
