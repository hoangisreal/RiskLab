"""One-shot initializer for the Docker demo stack (deploy/compose.demo.yml).

Runs migrations, provisions the least-privilege api/worker logins, builds the
synthetic scorer bundle and seeds the three demo accounts. Demo-only: fixed
demo passwords come from the demo Compose environment, every port stays on
loopback and this script refuses to run in a production environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
DEMO = Path(os.environ.get("RISKLAB_DEMO_ROOT", "/demo"))

ACCOUNTS = (
    ("ADMIN", "demo.admin", "RISKLAB_DEMO_ADMIN_PASSWORD", "RiskLabDemo-Admin1"),
    ("MANAGER", "demo.manager", "RISKLAB_DEMO_MANAGER_PASSWORD", "RiskLabDemo-Manager1"),
    ("ANALYST", "demo.analyst", "RISKLAB_DEMO_ANALYST_PASSWORD", "RiskLabDemo-Analyst1"),
)


def database_url(username: str, password: str) -> str:
    """Build a SQLAlchemy URL with proper password encoding (never string glue)."""
    from sqlalchemy import URL

    return URL.create(
        "postgresql+psycopg",
        username=username,
        password=password,
        host="postgres",
        port=5432,
        database="risklab",
    ).render_as_string(hide_password=False)


def owner_password() -> str:
    return os.environ["RISKLAB_DEMO_OWNER_DB_PASSWORD"]


def provision_logins() -> None:
    connect = {
        "host": "postgres",
        "port": 5432,
        "dbname": "risklab",
        "user": "risklab_migrator",
        "password": owner_password(),
    }
    with psycopg.connect(**connect, autocommit=True) as connection:
        for kind, group in (("api", "risklab_runtime"), ("worker", "risklab_worker")):
            login = f"risklab_{kind}_login"
            password = os.environ[f"RISKLAB_DEMO_{kind.upper()}_DB_PASSWORD"]
            if not connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=%s", (login,)
            ).fetchone():
                connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(login)))
            connection.execute(
                sql.SQL(
                    "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(login), sql.Literal(password))
            )
            connection.execute(
                sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(login))
            )
    print("Demo api/worker logins provisioned (least-privilege groups).")
    secrets_dir = Path(os.environ.get("RISKLAB_DEMO_SECRETS_DIR", "/demo-secrets"))
    for kind in ("api", "worker"):
        # One directory per service, mounted as separate volumes: the worker
        # must never be able to read the API credential or vice versa.
        kind_dir = secrets_dir / kind
        kind_dir.mkdir(parents=True, exist_ok=True)
        password = os.environ[f"RISKLAB_DEMO_{kind.upper()}_DB_PASSWORD"]
        target = kind_dir / f"{kind}_url"
        target.write_text(database_url(f"risklab_{kind}_login", password) + "\n")
        target.chmod(0o600)
    print("Demo database URL files written for api/worker (RISKLAB_DATABASE_URL_FILE).")


def verify_bundle(demo: Path) -> None:
    """Fail closed on a partial or corrupt bundle instead of reporting success."""
    try:
        for name in ("release.json", "model.pkl", "catalog.json", "comparison.json", "import.csv"):
            if not (demo / name).is_file():
                raise ValueError(f"missing {name}")
        if not (demo / "configs").is_dir() or not (demo / "sql").is_dir():
            raise ValueError("missing configs/ or sql/")
        from risklab.business.catalog import DemoCatalog, PinnedRelease
        from risklab.business.scoring import PinnedScoringModel

        catalog_bytes = (demo / "catalog.json").read_bytes()
        expected = hashlib.sha256(
            (ROOT / "tests/fixtures/business_v1/catalog.json").read_bytes()
        ).hexdigest()
        if hashlib.sha256(catalog_bytes).hexdigest() != expected:
            raise ValueError("catalog checksum differs from the committed fixture")
        catalog = DemoCatalog(catalog_bytes, expected_sha256=expected, config_dir=demo / "configs")
        release = PinnedRelease.model_validate_json((demo / "release.json").read_bytes())
        catalog.validate_release(release)
        PinnedScoringModel(
            model_path=demo / "model.pkl", release=release, config_dir=demo / "configs", root=demo
        )
    except Exception:
        raise SystemExit(
            "Demo bundle is incomplete or incompatible; run `make demo-reset` to rebuild it"
        ) from None


def build_bundle() -> None:
    if (DEMO / "release.json").exists():
        verify_bundle(DEMO)
        print("Demo bundle verified; keeping the pinned artifact.")
        return
    if DEMO.exists() and any(DEMO.iterdir()):
        raise SystemExit("Demo volume is incomplete; run `make demo-reset` to rebuild it")
    DEMO.mkdir(parents=True, exist_ok=True)
    from scripts.synthetic_application import build_scorer

    _, _, release, _ = build_scorer(DEMO)
    # The scorer recipe links sql/ to the repo; api/worker images have no repo
    # checkout, so ship a real copy inside the bundle volume instead.
    sql_link = DEMO / "sql"
    if sql_link.is_symlink():
        sql_link.unlink()
        shutil.copytree(ROOT / "sql", sql_link)
    release = release.model_copy(
        update={
            "model_version": "synthetic-docker-demo-v1",
            "model_run_id": "synthetic-docker-demo-train",
            "lock_id": "synthetic-docker-demo",
            "policy_version": "synthetic-docker-demo-policy-v1",
        }
    )
    fixtures = ROOT / "tests/fixtures/business_v1"
    (DEMO / "catalog.json").write_bytes((fixtures / "catalog.json").read_bytes())
    (DEMO / "import.csv").write_bytes((fixtures / "import-valid.csv").read_bytes())
    (DEMO / "comparison.json").write_text(
        json.dumps(
            {
                "report_version": "synthetic-docker-demo/1",
                "models": [],
                "data_manifest_hash": None,
                "partition_hash": None,
                "note": "Synthetic Docker demo model; no experiment comparison is claimed.",
            },
            indent=2,
        )
        + "\n"
    )
    (DEMO / "release.json").write_text(json.dumps(release.model_dump(), indent=2) + "\n")
    verify_bundle(DEMO)
    catalog_sha = hashlib.sha256((DEMO / "catalog.json").read_bytes()).hexdigest()
    print(f"Synthetic demo bundle built and verified (catalog sha256 {catalog_sha}).")


def seed_accounts() -> None:
    from pydantic import SecretStr

    from risklab.business.audit import append_audit
    from risklab.business.db import make_engine, schema_ready, session_factory
    from risklab.business.identity import HASHER, identity_lock, verify_password
    from risklab.business.models import User
    from risklab.business.settings import Settings

    settings = Settings(
        environment="development",
        database_url=SecretStr(database_url("risklab_migrator", owner_password())),
    )
    engine = make_engine(settings)
    assert engine is not None
    if not schema_ready(engine):
        raise SystemExit("Run migrations before seeding demo accounts")
    try:
        with session_factory(engine).begin() as session:
            identity_lock(session)
            for role, username, env_name, default in ACCOUNTS:
                password = os.environ.get(env_name, default)
                if len(password) < 12:
                    raise SystemExit(f"{env_name} must be at least 12 characters")
                existing = session.scalar(select(User).where(User.username == username))
                if existing is not None:
                    if existing.role != role or not existing.is_active:
                        raise SystemExit(f"Demo account {username} exists with different state")
                    if not verify_password(existing.password_hash, password):
                        raise SystemExit(
                            f"Demo account {username} exists with a different password; "
                            "run `make demo-reset` or align the demo password env"
                        )
                    continue
                user = User(username=username, role=role, password_hash=HASHER.hash(password))
                session.add(user)
                session.flush()
                append_audit(
                    session,
                    action="user.docker_demo_created",
                    resource_type="account",
                    resource_id=user.id,
                    actor_type="SYSTEM",
                    changes={"role": role},
                )
    finally:
        engine.dispose()
    print("Demo accounts ready: demo.admin / demo.manager / demo.analyst (demo-only passwords).")


def main() -> None:
    if os.environ.get("RISKLAB_ENVIRONMENT") == "production":
        raise SystemExit("Demo init must never run with RISKLAB_ENVIRONMENT=production")
    env = {
        **os.environ,
        "RISKLAB_DATABASE_URL": database_url("risklab_migrator", owner_password()),
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True
    )
    print("Migrations applied (alembic upgrade head).")
    provision_logins()
    build_bundle()
    if os.geteuid() == 0:
        # Init runs as root; api/worker run as the image's risklab user.
        secrets_dir = Path(os.environ.get("RISKLAB_DEMO_SECRETS_DIR", "/demo-secrets"))
        for base in (DEMO, secrets_dir):
            for path in (base, *base.rglob("*")):
                os.chown(path, 1000, 1000)
    seed_accounts()


if __name__ == "__main__":
    main()
