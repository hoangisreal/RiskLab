"""Import services against migrated PostgreSQL: atomicity, concurrency and private inputs."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from tests.business.conftest import migrate
from tests.conftest import CONFIG_DIR, REPO_ROOT

import risklab.business.imports as imports
from risklab.business.catalog import DemoCatalog, PinnedRelease, canonical_json
from risklab.business.db import session_factory
from risklab.business.errors import AppError
from risklab.business.models import Application, AuditEvent, ImportBatch, ImportIdempotency, User
from risklab.business.storage import LocalStorage

pytestmark = pytest.mark.integration
FIXTURES = REPO_ROOT / "tests/fixtures/business_v1"
HEADER = "external_reference,snapshot_id,supersedes_application_id\n"


@pytest.fixture
def import_env(database, tmp_path):
    factory = session_factory(database.runtime)
    actors = {}
    with factory.begin() as session:
        for name, role in [
            ("manager.one", "MANAGER"),
            ("manager.two", "MANAGER"),
            ("analyst.demo", "ANALYST"),
            ("admin.demo", "ADMIN"),
        ]:
            actor = User(username=name, role=role, password_hash="not-a-real-password-hash")
            session.add(actor)
            session.flush()
            actors[name] = actor.id
    data = (FIXTURES / "catalog.json").read_bytes()
    catalog = DemoCatalog(
        data, expected_sha256=hashlib.sha256(data).hexdigest(), config_dir=CONFIG_DIR
    )
    # Synthetic identity exercises persistence, not production model verification.
    release = PinnedRelease(
        model_version="synthetic-test-v1",
        model_sha256="a" * 64,
        model_run_id="synthetic-run",
        lock_id="synthetic-lock",
        feature_contract_version="1.1",
        feature_groups=["bureau", "previous", "installments"],
    )
    return factory, actors, catalog, release, LocalStorage(tmp_path / "objects")


def stage(env, data=None):
    _, _, catalog, release, storage = env
    data = (FIXTURES / "import-valid.csv").read_bytes() if data is None else data
    return imports.stage_upload(data, catalog=catalog, storage=storage, release=release)


def enqueue(env, *, key="import-key-001", data=None, actor="manager.one"):
    factory, actors, *_ = env
    staged = stage(env, data)
    with factory.begin() as session:
        return imports.enqueue_import(
            session, actor_id=actors[actor], key=key, staged=staged, request_id=uuid4()
        )


def prepare(env, data=None):
    _, _, catalog, release, storage = env
    data = (FIXTURES / "import-valid.csv").read_bytes() if data is None else data
    return catalog.prepare(data, release, storage)


def complete(env, batch, prepared=None):
    factory, *_ = env
    validated = prepare(env) if prepared is None else prepared
    with factory.begin() as session:
        return imports.persist_import(session, import_id=UUID(batch["id"]), prepared=validated)


def counts(factory):
    with factory() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (ImportBatch, ImportIdempotency, Application, AuditEvent)
        )


def test_valid_import_persists_private_source_identity_and_missing_vs_empty(import_env):
    factory, actors, catalog, _, storage = import_env
    original = enqueue(import_env)
    result = complete(import_env, original)
    assert result["state"] == "SUCCEEDED"
    assert (result["created_count"], result["ready_count"], result["incomplete_count"]) == (3, 2, 1)
    assert counts(factory) == (1, 1, 3, 5)
    with factory() as session:
        batch = session.get(ImportBatch, UUID(original["id"]))
        assert (
            storage.read(batch.catalog_sha256, max_bytes=20_000_000)
            == (FIXTURES / "catalog.json").read_bytes()
        )
        assert storage.read(batch.file_sha256) == (FIXTURES / "import-valid.csv").read_bytes()
        rows = {row.snapshot_id: row for row in session.scalars(select(Application))}
        for row in rows.values():
            raw = json.loads(storage.read(row.input_sha256))
            assert canonical_json(raw) == storage.read(row.input_sha256)
            assert (
                raw
                == json.loads((FIXTURES / "catalog.json").read_text())["snapshots"][row.snapshot_id]
            )
            assert row.catalog_version == catalog.version
            assert row.assignee_id is None and row.review_state == "UNASSIGNED"
            expected_scoring = "QUEUED" if row.feature_availability == "READY" else "NOT_REQUESTED"
            assert row.scoring_state == expected_scoring  # READY rows enqueue scoring atomically.
            assert row.input_revision == row.revision == 1
        assert rows["demo-missing-history"].missing_sources == ["bureau"]
        assert rows["demo-missing-history"].feature_availability == "INCOMPLETE"
        assert rows["demo-no-history"].missing_sources == []
        assert rows["demo-no-history"].feature_availability == "READY"
        assert session.scalar(select(ImportIdempotency)).actor_id == actors["manager.one"]
    # Completion is safe after a commit/response loss, without duplicating rows/audit.
    assert complete(import_env, original) == result
    assert enqueue(import_env) == original
    assert counts(factory) == (1, 1, 3, 5)


@pytest.mark.parametrize(
    "filename,code",
    [
        ("import-duplicate-reference.csv", "DUPLICATE_REFERENCE"),
        ("import-unknown-snapshot.csv", "UNKNOWN_SNAPSHOT"),
        ("import-path-injection.csv", "INVALID_REFERENCE"),
        ("import-extra-column.csv", "INVALID_HEADER"),
    ],
)
def test_invalid_csv_commits_failed_report_but_no_partial_applications(import_env, filename, code):
    data = (FIXTURES / filename).read_bytes()
    batch = enqueue(import_env, data=data)
    result = complete(import_env, batch, prepare(import_env, data))
    assert result["state"] == "FAILED"
    assert result["created_count"] == 0 and result["retryable"] is False
    assert code in {error["code"] for error in result["errors"]}
    assert counts(import_env[0]) == (1, 1, 0, 2)
    assert "../" not in str(result["errors"]) and "TARGET" not in str(result["errors"])


def test_invalid_raw_and_dangling_history_fail_entire_file(import_env):
    factory, _, _, release, storage = import_env
    document = json.loads((FIXTURES / "catalog.json").read_text())
    document["snapshots"]["demo-no-history"] = json.loads(
        (FIXTURES / "invalid-history.json").read_text()
    )
    document["snapshots"]["demo-history"]["history"]["installments"]["records"][0]["prev_ref"] = (
        "unknown-parent"
    )
    data = canonical_json(document)
    catalog = DemoCatalog(
        data, expected_sha256=hashlib.sha256(data).hexdigest(), config_dir=CONFIG_DIR
    )
    env = (factory, import_env[1], catalog, release, storage)
    batch = enqueue(env)
    validated = prepare(env)
    assert len(validated.issues) == 2
    result = complete(env, batch, validated)
    assert result["state"] == "FAILED"
    assert all(issue["code"] == "INVALID_SNAPSHOT" for issue in result["errors"])
    assert counts(factory) == (1, 1, 0, 2)


def test_replacement_link_preserves_original_and_authorizes_current_manager(import_env):
    factory, actors, *_ = import_env
    original = enqueue(import_env)
    complete(import_env, original)
    with factory() as session:
        source = session.scalar(select(Application))
        source_id = source.id
        before = {
            column.name: getattr(source, column.name) for column in Application.__table__.columns
        }
    data = (HEADER + f"REPLACEMENT,demo-no-history,{source_id}\n").encode()
    batch = enqueue(import_env, key="replacement-001", data=data, actor="manager.two")
    assert complete(import_env, batch, prepare(import_env, data))["state"] == "SUCCEEDED"
    with factory() as session:
        source = session.get(Application, source_id)
        assert {
            column.name: getattr(source, column.name) for column in Application.__table__.columns
        } == before
        newer = session.scalar(
            select(Application).where(Application.import_id == UUID(batch["id"]))
        )
        assert newer.supersedes_application_id == source_id
    for actor in ("admin.demo", "analyst.demo"):
        with pytest.raises(AppError) as error:
            enqueue(import_env, key="forbidden-001", data=data, actor=actor)
        assert error.value.status == 403
    # A previously valid intent cannot acquire access after initiator is disabled/demoted.
    pending = enqueue(import_env, key="replacement-002", data=data)
    with factory.begin() as session:
        session.get(User, actors["manager.one"]).role = "ANALYST"
    with pytest.raises(AppError) as error:
        complete(import_env, pending, prepare(import_env, data))
    assert error.value.status == 403
    with factory() as session:
        assert session.get(ImportBatch, UUID(pending["id"])).state == "QUEUED"


def test_unknown_replacement_rolls_back_all_rows_to_safe_failure(import_env):
    data = (HEADER + "OK,demo-history,\n" + f"MISSING,demo-no-history,{uuid4()}\n").encode()
    batch = enqueue(import_env, data=data)
    result = complete(import_env, batch, prepare(import_env, data))
    assert result["state"] == "FAILED"
    assert result["errors"] == [
        {"row": 3, "field": "supersedes_application_id", "code": "INVALID_REPLACEMENT"}
    ]
    assert counts(import_env[0]) == (1, 1, 0, 2)


def test_duplicate_concurrent_requests_converge_and_original_response_survives_completion(
    import_env,
):
    factory, actors, *_ = import_env
    staged = stage(import_env)
    barrier = Barrier(2)

    def request(_):
        with factory.begin() as session:
            # Match the shared auth lock a future HTTP consumer takes before enqueue.
            session.execute(
                select(User.id).where(User.id == actors["manager.one"]).with_for_update(read=True)
            )
            barrier.wait(timeout=10)
            return imports.enqueue_import(
                session,
                actor_id=actors["manager.one"],
                key="concurrent-001",
                staged=staged,
                request_id=uuid4(),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(request, range(2)))
    assert first == second
    assert counts(factory) == (1, 1, 0, 1)
    complete(import_env, first)
    assert enqueue(import_env, key="concurrent-001") == first
    assert counts(factory) == (1, 1, 3, 5)


def test_same_key_different_payload_conflicts_and_actor_scopes_are_independent(import_env):
    first = enqueue(import_env)
    with pytest.raises(AppError) as error:
        enqueue(import_env, data=(HEADER + "DIFFERENT,demo-no-history,\n").encode())
    assert error.value.status == 409
    second = enqueue(import_env, actor="manager.two")
    assert first["id"] != second["id"]
    assert counts(import_env[0]) == (2, 2, 0, 2)


@pytest.mark.parametrize("change,status", [("disable", 401), ("ADMIN", 403), ("ANALYST", 403)])
def test_replay_checks_current_account_and_role_before_returning_original(
    import_env, change, status
):
    factory, actors, *_ = import_env
    enqueue(import_env)
    with factory.begin() as session:
        actor = session.get(User, actors["manager.one"])
        if change == "disable":
            actor.is_active = False
        else:
            actor.role = change
    with pytest.raises(AppError) as error:
        enqueue(import_env)
    assert error.value.status == status
    assert counts(factory) == (1, 1, 0, 1)


def test_distinct_keys_are_distinct_intents_and_replay_does_not_repin_model(import_env):
    factory, actors, catalog, release, storage = import_env
    first = enqueue(import_env)
    second = enqueue(import_env, key="import-key-002")
    assert first["id"] != second["id"]
    newer = release.model_copy(
        update={"model_version": "synthetic-test-v2", "model_sha256": "b" * 64}
    )
    new_staged = imports.stage_upload(
        (FIXTURES / "import-valid.csv").read_bytes(),
        catalog=catalog,
        release=newer,
        storage=storage,
    )
    with factory.begin() as session:
        replay = imports.enqueue_import(
            session,
            actor_id=actors["manager.one"],
            key="import-key-001",
            staged=new_staged,
            request_id=uuid4(),
        )
    assert replay == first
    with factory() as session:
        assert (
            session.get(ImportBatch, UUID(first["id"])).release_identity["model_version"]
            == release.model_version
        )
    assert counts(factory) == (2, 2, 0, 2)


@pytest.mark.parametrize("key", ["short", "../attempt-to-read", "a" * 129, "spaces not allowed"])
def test_bad_idempotency_keys_are_rejected_without_persistence(import_env, key):
    with pytest.raises(AppError) as error:
        enqueue(import_env, key=key)
    assert error.value.status == 422
    assert counts(import_env[0]) == (0, 0, 0, 0)


def test_different_payloads_racing_on_same_key_only_one_wins(import_env):
    factory, actors, *_ = import_env
    envelopes = [
        stage(import_env, (HEADER + f"REF-{i},demo-no-history,\n").encode()) for i in range(2)
    ]
    barrier = Barrier(2)

    def request(staged):
        try:
            with factory.begin() as session:
                barrier.wait(timeout=10)
                imports.enqueue_import(
                    session,
                    actor_id=actors["manager.one"],
                    key="race-payload-001",
                    staged=staged,
                    request_id=uuid4(),
                )
            return 202
        except AppError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(request, envelopes)) == [202, 409]
    assert counts(factory) == (1, 1, 0, 1)


def test_concurrent_different_keys_enforce_active_cap_but_allow_replay(import_env):
    factory, actors, *_ = import_env
    first = enqueue(import_env)
    staged = stage(import_env)
    barrier = Barrier(2)

    def request(key):
        try:
            with factory.begin() as session:
                barrier.wait(timeout=10)
                imports.enqueue_import(
                    session,
                    actor_id=actors["manager.one"],
                    key=key,
                    staged=staged,
                    request_id=uuid4(),
                )
            return 202
        except AppError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(request, ["cap-race-001", "cap-race-002"])) == [202, 429]
    assert enqueue(import_env) == first
    assert counts(factory) == (2, 2, 0, 2)


@pytest.mark.parametrize(
    "point", ["enqueue-audit", "enqueue-idempotency", "row-audit", "completion-audit"]
)
def test_failures_at_side_effect_boundaries_rollback_then_retry(import_env, monkeypatch, point):
    factory, actors, *_ = import_env
    original_audit = imports.append_audit

    def fail_audit(session, **arguments):
        result = original_audit(session, **arguments)
        session.flush()
        action = arguments["action"]
        if (
            (point == "enqueue-audit" and action == "import.created")
            or (point == "row-audit" and action == "application.imported")
            or (point == "completion-audit" and action == "import.completed")
        ):
            raise RuntimeError("Injected after side effects before commit")
        return result

    monkeypatch.setattr(imports, "append_audit", fail_audit)
    if point.startswith("enqueue"):
        if point == "enqueue-idempotency":

            def fail_insert(mapper, connection, target):
                raise RuntimeError("Injected while recording original response")

            event.listen(ImportIdempotency, "before_insert", fail_insert)
        try:
            with pytest.raises(RuntimeError):
                enqueue(import_env)
        finally:
            if point == "enqueue-idempotency":
                event.remove(ImportIdempotency, "before_insert", fail_insert)
        assert counts(factory) == (0, 0, 0, 0)
        monkeypatch.setattr(imports, "append_audit", original_audit)
        batch = enqueue(import_env)
    else:
        batch = enqueue(import_env)
        with pytest.raises(RuntimeError):
            complete(import_env, batch)
        assert counts(factory) == (1, 1, 0, 1)
        with factory() as session:
            assert session.get(ImportBatch, UUID(batch["id"])).state == "QUEUED"
        monkeypatch.setattr(imports, "append_audit", original_audit)
    assert complete(import_env, batch)["state"] == "SUCCEEDED"
    assert counts(factory) == (1, 1, 3, 5)


def test_concurrent_completion_is_atomic_and_input_mismatch_is_rejected(import_env):
    factory, *_ = import_env
    batch = enqueue(import_env)
    validated = prepare(import_env)
    with pytest.raises(AppError) as error:
        complete(import_env, batch, replace(validated, file_sha256="b" * 64))
    assert error.value.status == 409
    barrier = Barrier(2)

    def finish(_):
        barrier.wait(timeout=10)
        return complete(import_env, batch, validated)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(finish, range(2)))
    assert first == second
    assert counts(factory) == (1, 1, 3, 5)


def test_completion_refreshes_an_already_loaded_batch_after_another_commit(import_env):
    factory, *_ = import_env
    batch = enqueue(import_env)
    prepared = prepare(import_env)
    with factory() as stale:
        old = stale.get(ImportBatch, UUID(batch["id"]))
        result = complete(import_env, batch, prepared)
        assert old.state == "QUEUED"
        assert (
            imports.persist_import(stale, import_id=UUID(batch["id"]), prepared=prepared) == result
        )
        stale.commit()
    assert counts(factory) == (1, 1, 3, 5)


@pytest.mark.parametrize("size,expected", [(0, "FAILED"), (100, "SUCCEEDED"), (101, "FAILED")])
def test_persistence_respects_csv_row_boundaries(import_env, size, expected):
    data = (HEADER + "".join(f"REF-{number},demo-no-history,\n" for number in range(size))).encode()
    batch = enqueue(import_env, data=data)
    result = complete(import_env, batch, prepare(import_env, data))
    assert result["state"] == expected
    assert result["created_count"] == (size if expected == "SUCCEEDED" else 0)
    if expected == "FAILED":
        assert result["errors"][0]["code"] == "ROW_LIMIT_EXCEEDED"


def test_failed_report_is_bounded_and_does_not_leak_cells(import_env):
    data = (HEADER + "".join("sensitive cell,unknown/path,invalid\n" for _ in range(100))).encode()
    batch = enqueue(import_env, data=data)
    result = complete(import_env, batch, prepare(import_env, data))
    assert result["state"] == "FAILED" and result["errors_truncated"]
    assert len(result["errors"]) == 100
    assert "sensitive cell" not in str(result)
    assert "unknown/path" not in str(result)
    assert counts(import_env[0]) == (1, 1, 0, 2)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE applications SET input_sha256 = repeat('b',64)",
        "UPDATE applications SET snapshot_id = 'changed'",
        "UPDATE applications SET supersedes_application_id = NULL",
        "DELETE FROM applications",
        "UPDATE imports SET file_sha256 = repeat('b',64)",
        "UPDATE imports SET actor_id = gen_random_uuid()",
        "UPDATE import_idempotency SET response = '{}'",
        "DELETE FROM import_idempotency",
    ],
)
def test_runtime_cannot_mutate_immutable_input_or_idempotency(database, statement):
    with pytest.raises(DBAPIError), database.runtime.begin() as connection:
        connection.execute(text(statement))


def test_database_uniqueness_and_row_constraints(import_env, database):
    factory, actors, *_ = import_env
    batch = enqueue(import_env)
    complete(import_env, batch)
    with pytest.raises(IntegrityError), database.runtime.begin() as connection:
        connection.execute(text("UPDATE applications SET revision=0"))
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(
            ImportIdempotency(
                actor_id=actors["manager.one"],
                operation="imports.create",
                key="import-key-001",
                payload_sha256="a" * 64,
                import_id=UUID(batch["id"]),
                status=202,
                response={},
            )
        )
        session.flush()
    with factory() as session:
        row = session.scalar(select(Application))
        values = {
            column.name: getattr(row, column.name) for column in Application.__table__.columns
        }
    values["id"] = uuid4()
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(Application(**values))
        session.flush()


@pytest.mark.parametrize("database", ["0002_login_limits"], indirect=True)
def test_upgrade_preserves_accounts_and_adds_import_tables(database):
    user_id = uuid4()
    with session_factory(database.runtime).begin() as session:
        session.add(
            User(id=user_id, username="existing.manager", role="MANAGER", password_hash="test-only")
        )
    migrate(database.owner)
    with session_factory(database.runtime)() as session:
        assert session.get(User, user_id).username == "existing.manager"
        assert session.scalar(select(func.count()).select_from(ImportBatch)) == 0
