"""End-to-end business workflow over real PostgreSQL, storage and a synthetic model.

Covers B03 import -> worker scoring -> persisted prediction, B04 review and
decision guards, queue fencing and B05 report scoping. The fitted pipeline is
a synthetic test artifact only, never a production bundle identity.
"""

from __future__ import annotations

import hashlib
import pickle
import shutil
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import timedelta
from threading import Event
from uuid import UUID

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import OperationalError
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
    ImportBatch,
    Job,
    Prediction,
    User,
    now_utc,
)
from risklab.business.scoring import PinnedScoringModel, runtime_protocol_hashes
from risklab.business.settings import Settings
from risklab.business.storage import LocalStorage
from risklab.business.worker import WorkerRuntime, run_once
from risklab.cli import app as cli_app
from risklab.config import load_data_config, load_partition_config
from risklab.features.application import FeatureColumns
from risklab.features.historical import combined_columns, load_full_frame
from risklab.manifest import library_versions
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
    X, y = load_full_frame(
        "train",
        "fit",
        ["bureau", "previous", "installments"],
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
        config_dir=cfg_dir,
    )
    numeric, categorical = combined_columns(
        ["bureau", "previous", "installments"], config_dir=cfg_dir
    )
    columns = FeatureColumns(numeric=numeric, categorical=categorical, log=[])
    pipe = fit_pipeline(
        make_lgbm_pipeline(load_lgbm_params(cfg_dir), columns=columns, config_dir=str(cfg_dir)),
        X[columns.all].head(500),
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
        protocol_hashes=runtime_protocol_hashes(root, cfg_dir),
        library_versions=library_versions(),
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
    return app, factory, users, runtime, database.worker


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
    # Assignee counts expose the username for display, never the raw UUID.
    assignee_values = {row["value"] for row in report["by_assignee"]}
    assert "analyst.demo" in assignee_values
    assert str(users["analyst.demo"]) not in assignee_values
    analyst = signed_in(app, "analyst.demo")
    scoped = analyst.get("/api/v1/reports/workload").json()["data"]
    assert scoped["total"] == 1
    assert {row["value"] for row in scoped["by_assignee"]} == {"analyst.demo"}
    # Risk distribution is aggregated, scoped and role-gated like the workload.
    distribution = manager.get("/api/v1/reports/risk-distribution").json()["data"]
    assert distribution["total_scored"] == 2
    assert len(distribution["buckets"]) == 10
    assert sum(bucket["count"] for bucket in distribution["buckets"]) == 2
    scoped_distribution = analyst.get("/api/v1/reports/risk-distribution").json()["data"]
    assert scoped_distribution["total_scored"] == 1
    assert signed_in(app, "admin.demo").get("/api/v1/reports/workload").status_code == 403
    assert signed_in(app, "admin.demo").get("/api/v1/reports/risk-distribution").status_code == 403
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


@pytest.mark.parametrize("actor", [None, "analyst.demo", "admin.demo", "manager.demo"])
def test_rejected_import_never_stages_objects(workflow_env, actor):
    app, factory, users, runtime, engine = workflow_env
    client = TestClient(app) if actor is None else signed_in(app, actor)
    if actor == "manager.demo":
        client.headers["x-csrf-token"] = "invalid"
    before = set(runtime.storage.root.iterdir())
    response = client.post(
        "/api/v1/imports",
        headers={"Idempotency-Key": "unauthorized-import"},
        data={"import_contract_version": "demo-import/1", "catalog_version": "synthetic-b00-v1"},
        files={"file": ("demo.csv", b"unauthorized-unique-upload", "text/csv")},
    )
    assert response.status_code in {401, 403}
    assert set(runtime.storage.root.iterdir()) == before


def test_old_draft_cannot_be_edited_or_submitted_after_reassignment(workflow_env):
    app, factory, users, runtime, engine = workflow_env
    manager, _ = import_and_score(workflow_env)
    target = scored_application(workflow_env, manager)
    assigned = assign(manager, target, users["analyst.demo"])
    analyst = signed_in(app, "analyst.demo")
    first = analyst.post(
        f"/api/v1/applications/{target.id}/reviews",
        json={"expected_revision": assigned["revision"]},
    ).json()["data"]
    old_id = first["review"]["id"]
    saved = analyst.patch(
        f"/api/v1/applications/{target.id}/reviews/{old_id}",
        json={
            "expected_revision": first["application_revision"],
            "notes": "Old draft",
            "proposal": "APPROVE",
        },
    )
    assert saved.status_code == 200
    assign(manager, target, users["analyst.two"])
    reassigned = assign(manager, target, users["analyst.demo"])
    current = analyst.post(
        f"/api/v1/applications/{target.id}/reviews",
        json={"expected_revision": reassigned["revision"]},
    ).json()["data"]
    revision = current["application_revision"]
    rejected = analyst.patch(
        f"/api/v1/applications/{target.id}/reviews/{old_id}",
        json={"expected_revision": revision, "notes": "Stale edit", "proposal": "DECLINE"},
    )
    assert rejected.status_code == 409
    rejected = analyst.post(
        f"/api/v1/applications/{target.id}/reviews/{old_id}/submit",
        json={"expected_revision": revision},
    )
    assert rejected.status_code == 409
    detail = analyst.get(f"/api/v1/applications/{target.id}").json()["data"]
    assert detail["revision"] == revision
    assert detail["current_review"]["id"] == current["review"]["id"]


@pytest.mark.parametrize("key", ["short", "contains spaces", "x" * 129])
def test_decision_rejects_invalid_idempotency_key_before_db_write(workflow_env, key):
    app, factory, users, runtime, engine = workflow_env
    manager, _ = import_and_score(workflow_env)
    target = scored_application(workflow_env, manager)
    assign(manager, target, users["analyst.demo"])
    submitted = submit_as(signed_in(app, "analyst.demo"), target.id)
    response = manager.post(
        f"/api/v1/applications/{target.id}/decisions",
        headers={"Idempotency-Key": key},
        json={
            "expected_revision": submitted["application_revision"],
            "review_id": submitted["review"]["id"],
            "outcome": "APPROVED_SIMULATED",
            "reason": "Test validation",
        },
    )
    assert response.status_code == 422
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Decision)) == 0


@pytest.mark.parametrize(
    "change", ["protocol", "versions", "missing_versions", "groups", "duplicates", "columns"]
)
def test_pinned_model_rejects_incompatible_release_before_deserialization(
    model_env, monkeypatch, change
):
    import risklab.business.scoring as scoring
    from risklab.business.errors import AppError

    cfg_dir, root, release, scorer = model_env
    updates = {
        "protocol": {"protocol_hashes": {"wrong": "0" * 64}},
        "versions": {"library_versions": {**release.library_versions, "python": "0.0"}},
        "missing_versions": {"library_versions": {}},
        "groups": {"feature_groups": ["unknown"]},
        "duplicates": {"feature_groups": ["bureau", "bureau"]},
        "columns": {"feature_groups": []},
    }
    monkeypatch.setattr(scoring.pickle, "loads", lambda _: pytest.fail("loaded incompatible model"))
    with pytest.raises(AppError, match="Model"):
        PinnedScoringModel(
            model_path=root / "model.pkl",
            release=release.model_copy(update=updates[change]),
            config_dir=cfg_dir,
            root=root,
        )


@pytest.mark.parametrize("kind", ["IMPORT", "SCORING"])
def test_lease_expiry_during_result_commit_rolls_back_all_side_effects(
    workflow_env, monkeypatch, kind
):
    import risklab.business.worker as worker

    app, factory, users, runtime, engine = workflow_env
    manager = signed_in(app, "manager.demo")
    response = manager.post(
        "/api/v1/imports",
        headers={"Idempotency-Key": "commit-expiry-test"},
        data={"import_contract_version": "demo-import/1", "catalog_version": "synthetic-b00-v1"},
        files={"file": ("demo.csv", (FIXTURES / "import-valid.csv").read_bytes(), "text/csv")},
    )
    assert response.status_code == 202
    if kind == "SCORING":
        assert run_once(engine, runtime, worker_id="prepare")
    original = worker.complete_job

    def expired(session, **kwargs):
        if session.get(Job, kwargs["job_id"]).kind == kind:
            return False
        return original(session, **kwargs)

    monkeypatch.setattr(worker, "complete_job", expired)
    assert run_once(engine, runtime, worker_id="expiring")
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Prediction)) == 0
        if kind == "IMPORT":
            assert session.scalar(select(func.count()).select_from(Application)) == 0
        assert not session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "application.scored")
        )


def test_alternate_unverified_history_config_is_rejected(model_env, tmp_path):
    from risklab.business.errors import AppError
    from risklab.business.scoring import runtime_protocol_hashes

    cfg_dir, root, *_ = model_env
    alternate = tmp_path / "configs"
    shutil.copytree(cfg_dir, alternate)
    with pytest.raises(AppError, match="verified root"):
        runtime_protocol_hashes(root, alternate)


def test_missing_model_preserves_queued_scoring_budget(workflow_env):
    from dataclasses import replace

    app, factory, users, runtime, engine = workflow_env
    manager = signed_in(app, "manager.demo")
    response = manager.post(
        "/api/v1/imports",
        headers={"Idempotency-Key": "no-model-import-001"},
        data={"import_contract_version": "demo-import/1", "catalog_version": "synthetic-b00-v1"},
        files={"file": ("demo.csv", (FIXTURES / "import-valid.csv").read_bytes(), "text/csv")},
    )
    assert response.status_code == 202
    unavailable = replace(runtime, scorer=None)
    assert run_once(engine, unavailable, worker_id="no-model")
    for _ in range(4):
        assert not run_once(engine, unavailable, worker_id="no-model")
    with factory() as session:
        scoring = list(session.scalars(select(Job).where(Job.kind == "SCORING")))
        assert len(scoring) == 2
        assert all(j.state == "QUEUED" and j.attempt == 0 for j in scoring)
        import_job = session.scalar(select(Job).where(Job.kind == "IMPORT"))
        imported = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action.in_(["application.imported", "import.completed"])
                )
            )
        )
        assert len(imported) == 4 and all(e.job_id == import_job.id for e in imported)
    assert run_once(engine, runtime, worker_id="model-ready")


def test_invalid_import_key_never_stages_files(workflow_env):
    app, factory, users, runtime, engine = workflow_env
    manager = signed_in(app, "manager.demo")
    before = set(runtime.storage.root.iterdir())
    result = manager.post(
        "/api/v1/imports",
        headers={"Idempotency-Key": "bad"},
        data={"import_contract_version": "demo-import/1", "catalog_version": "synthetic-b00-v1"},
        files={"file": ("demo.csv", b"must-not-stage", "text/csv")},
    )
    assert result.status_code == 422
    assert set(runtime.storage.root.iterdir()) == before


@pytest.mark.parametrize("operation", ["assign", "return"])
@pytest.mark.parametrize("disable_first", [True, False])
def test_assignee_status_serializes_with_assignment_and_return(
    workflow_env, database, operation, disable_first
):
    app, factory, users, _, _ = workflow_env
    manager, _ = import_and_score(workflow_env)
    target = scored_application(workflow_env, manager)
    assignee_id = users["analyst.demo"]
    review_id = None
    if operation == "return":
        assign(manager, target, assignee_id)
        analyst = signed_in(app, "analyst.demo")
        submitted = submit_as(analyst, target.id)
        review_id = submitted["review"]["id"]
    detail = manager.get(f"/api/v1/applications/{target.id}").json()["data"]
    body = {"expected_revision": detail["revision"], "reason": "Concurrent status test"}
    if operation == "assign":
        body["assignee_id"] = str(assignee_id)
    else:
        body["review_id"] = review_id

    reached = Event()
    resume = Event()

    def observe(connection, cursor, statement, parameters, context, executemany):
        if (
            "FROM users" in statement
            and "FOR SHARE" in statement
            and assignee_id in parameters.values()
        ):
            reached.set()
            if not disable_first:
                assert resume.wait(10)

    # Observe after SELECT when Manager wins, before SELECT when Admin wins.
    hook = "before_cursor_execute" if disable_first else "after_cursor_execute"
    event.listen(database.runtime, hook, observe)

    def mutate():
        if operation == "assign":
            return manager.put(f"/api/v1/applications/{target.id}/assignment", json=body)
        return manager.post(f"/api/v1/applications/{target.id}/return", json=body)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            if disable_first:
                with database.owner.begin() as connection:
                    connection.execute(
                        text("UPDATE users SET is_active = false WHERE id = :id"),
                        {"id": assignee_id},
                    )
                    result = pool.submit(mutate)
                    assert reached.wait(10)
                    with pytest.raises(TimeoutError):
                        result.result(timeout=0.1)
                response = result.result(timeout=10)
                assert response.status_code == (422 if operation == "assign" else 409)
            else:
                result = pool.submit(mutate)
                try:
                    assert reached.wait(10)
                    # The same target row lock used by Admin cannot be acquired.
                    import psycopg

                    with database.owner.connect() as connection:
                        with pytest.raises(OperationalError) as blocked:
                            connection.execute(
                                text("SELECT id FROM users WHERE id = :id FOR UPDATE NOWAIT"),
                                {"id": assignee_id},
                            )
                        assert isinstance(blocked.value.orig, psycopg.errors.LockNotAvailable)
                finally:
                    resume.set()
                assert result.result(timeout=10).status_code == 200
                with database.owner.begin() as connection:
                    connection.execute(
                        text("UPDATE users SET is_active = false WHERE id = :id"),
                        {"id": assignee_id},
                    )
    finally:
        resume.set()
        event.remove(database.runtime, hook, observe)
    with factory() as session:
        updated = session.get(Application, target.id)
        assert updated.revision == detail["revision"] + (0 if disable_first else 1)


@pytest.mark.parametrize("kind", ["IMPORT", "SCORING"])
def test_http_job_creation_audit_references_the_enqueued_job(workflow_env, kind):
    app, factory, _, runtime, engine = workflow_env
    manager = signed_in(app, "manager.demo")
    imported = manager.post(
        "/api/v1/imports",
        headers={"Idempotency-Key": "audit-job-create-001"},
        data={"import_contract_version": "demo-import/1", "catalog_version": "synthetic-b00-v1"},
        files={"file": ("demo.csv", (FIXTURES / "import-valid.csv").read_bytes(), "text/csv")},
    )
    assert imported.status_code == 202
    batch_id = UUID(imported.json()["data"]["id"])
    with factory() as session:
        created = session.scalar(select(AuditEvent).where(AuditEvent.action == "import.created"))
        assert session.get(Job, created.job_id).import_id == batch_id
    if kind == "SCORING":
        assert run_once(engine, runtime, worker_id="audit-job-import")
    with factory.begin() as session:
        for _ in range(3):
            claimed = claim_job(session, worker_id="audit-failure", kinds=(kind,))
            state = fail_job(
                session,
                job_id=claimed.id,
                attempt=claimed.attempt,
                worker_id="audit-failure",
                error_code="DEPENDENCY_UNAVAILABLE",
                retryable=True,
            )
            claimed.available_at = now_utc()
        assert state == "FAILED"
        if kind == "IMPORT":
            target = session.get(ImportBatch, batch_id)
            path = f"/api/v1/imports/{target.id}/retry"
            action = "import.retried"
        else:
            target = session.get(Application, claimed.application_id)
            path = f"/api/v1/applications/{target.id}/scoring/retry"
            action = "scoring.retry_requested"
        resource_id = target.id
        revision = target.revision
    retried = manager.post(path, json={"expected_revision": revision, "reason": "Audit retry test"})
    assert retried.status_code == 202, retried.text
    with factory() as session:
        audit = session.scalar(select(AuditEvent).where(AuditEvent.action == action))
        job = session.get(Job, audit.job_id)
        assert job is not None and job.kind == kind and job.state == "QUEUED"
        assert (job.import_id if kind == "IMPORT" else job.application_id) == resource_id
