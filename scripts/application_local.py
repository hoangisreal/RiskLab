"""Prepare and run the complete loopback-only synthetic application locally."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url

from risklab.business.audit import append_audit
from risklab.business.catalog import PinnedRelease
from risklab.business.db import make_engine, schema_ready, session_factory
from risklab.business.identity import HASHER, identity_lock, verify_password
from risklab.business.models import User
from risklab.business.scoring import PinnedScoringModel
from risklab.business.settings import load_settings

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data/application/local-demo"
RUN = ROOT / "reports/operations/application-local"
ACCOUNTS = ROOT / ".secrets/local_demo_accounts.json"


def private_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    temporary.replace(path)


def database_env(kind: str) -> dict[str, str]:
    filename = ROOT / f".secrets/local_{kind}_url"
    url = make_url(filename.read_text().strip())
    if (url.host, url.port, url.database) != ("127.0.0.1", 15432, "risklab"):
        raise RuntimeError("Expected the dedicated loopback RiskLab database")
    return {"RISKLAB_DATABASE_URL_FILE": str(filename)}


def demo_env() -> dict[str, str]:
    return {
        "RISKLAB_ENVIRONMENT": "development",
        "RISKLAB_ORIGIN": "http://localhost:3000",
        "RISKLAB_APP_ROOT": str(DEMO),
        "RISKLAB_CONFIG_DIR": str(DEMO / "configs"),
        "RISKLAB_STORAGE_ROOT": str(DEMO / "objects"),
        "RISKLAB_MODEL_FILE": str(DEMO / "model.pkl"),
        "RISKLAB_RELEASE_FILE": str(DEMO / "release.json"),
        "RISKLAB_CATALOG_FILE": str(DEMO / "catalog.json"),
        "RISKLAB_CATALOG_SHA256": hashlib.sha256((DEMO / "catalog.json").read_bytes()).hexdigest(),
        "RISKLAB_COMPARISON_FILE": str(DEMO / "comparison.json"),
    }


def seed_accounts() -> None:
    engine = make_engine(load_settings(database_env("owner")))
    assert engine is not None
    if not schema_ready(engine):
        raise RuntimeError("Run make app-db app-migrate first")
    credentials = (
        json.loads(ACCOUNTS.read_text())
        if ACCOUNTS.exists()
        else {
            role: {"username": f"local.{role.lower()}", "password": secrets.token_urlsafe(24)}
            for role in ("ADMIN", "MANAGER", "ANALYST")
        }
    )
    try:
        with session_factory(engine).begin() as session:
            identity_lock(session)
            for role, account in credentials.items():
                existing = session.scalar(select(User).where(User.username == account["username"]))
                if existing is not None and (
                    existing.role != role
                    or not existing.is_active
                    or not verify_password(existing.password_hash, account["password"])
                ):
                    raise RuntimeError(
                        "A local demo account already exists with different credentials"
                    )
            # Save generated passwords before committing accounts so interrupted
            # preparation can be retried without resetting existing identities.
            private_json(ACCOUNTS, credentials)
            for role, account in credentials.items():
                if session.scalar(select(User.id).where(User.username == account["username"])):
                    continue
                user = User(
                    username=account["username"],
                    role=role,
                    password_hash=HASHER.hash(account["password"]),
                )
                session.add(user)
                session.flush()
                append_audit(
                    session,
                    action="user.local_demo_created",
                    resource_type="account",
                    resource_id=user.id,
                    actor_type="SYSTEM",
                    changes={"role": role},
                )
    finally:
        engine.dispose()


def prepare() -> None:
    if not (DEMO / "release.json").exists():
        if DEMO.exists() and any(DEMO.iterdir()):
            raise RuntimeError("Incomplete demo directory exists; inspect it before rebuilding")
        from scripts.synthetic_application import build_scorer

        DEMO.mkdir(parents=True, exist_ok=True, mode=0o700)
        _, _, release, _ = build_scorer(DEMO)
        release = release.model_copy(
            update={
                "model_version": "synthetic-local-v1",
                "model_run_id": "synthetic-local-train",
                "lock_id": "synthetic-local-demo",
                "policy_version": "synthetic-local-policy-v1",
            }
        )
        (DEMO / "catalog.json").write_bytes(
            (ROOT / "tests/fixtures/business_v1/catalog.json").read_bytes()
        )
        (DEMO / "import.csv").write_bytes(
            (ROOT / "tests/fixtures/business_v1/import-valid.csv").read_bytes()
        )
        private_json(
            DEMO / "comparison.json",
            {
                "report_version": "synthetic-local-demo/1",
                "models": [],
                "data_manifest_hash": None,
                "partition_hash": None,
                "note": "Synthetic local smoke model; no experiment comparison is claimed.",
            },
        )
        private_json(DEMO / "release.json", release.model_dump())
    verify_model()
    seed_accounts()
    print("Synthetic model/catalog ready. Import CSV: data/application/local-demo/import.csv")
    print("Local account credentials: .secrets/local_demo_accounts.json (private)")


def verify_model() -> None:
    for name in ("release.json", "model.pkl", "catalog.json", "comparison.json", "import.csv"):
        if not (DEMO / name).is_file():
            raise RuntimeError(f"Local demo is incomplete: missing {name}")
    from risklab.business.catalog import DemoCatalog

    catalog_bytes = (DEMO / "catalog.json").read_bytes()
    expected = hashlib.sha256(
        (ROOT / "tests/fixtures/business_v1/catalog.json").read_bytes()
    ).hexdigest()
    catalog = DemoCatalog(catalog_bytes, expected_sha256=expected, config_dir=DEMO / "configs")
    release = PinnedRelease.model_validate_json((DEMO / "release.json").read_bytes())
    catalog.validate_release(release)
    PinnedScoringModel(
        model_path=DEMO / "model.pkl", release=release, config_dir=DEMO / "configs", root=DEMO
    )


def process_identity(pid: int) -> str | None:
    try:
        # /proc start ticks distinguish an owned process from a reused PID.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except (OSError, IndexError):
        return None


def alive(record: dict) -> bool:
    return (
        record.get("start_ticks") is not None
        and process_identity(record["pid"]) == record["start_ticks"]
    )


def stop_process(record: dict) -> None:
    if not alive(record):
        return
    with suppress(ProcessLookupError):
        os.killpg(record["pid"], signal.SIGTERM)
    deadline = time.monotonic() + 10
    while alive(record) and time.monotonic() < deadline:
        time.sleep(0.1)
    if alive(record):
        with suppress(ProcessLookupError):
            os.killpg(record["pid"], signal.SIGKILL)
        deadline = time.monotonic() + 2
        while alive(record) and time.monotonic() < deadline:
            time.sleep(0.05)


def http_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def start() -> None:
    state_file = RUN / "processes.json"
    old = json.loads(state_file.read_text()) if state_file.exists() else {}
    if any(alive(record) for record in old.values()):
        raise RuntimeError("Local processes already running; use status or stop first")
    verify_model()
    if not (ROOT / "apps/web/.next/BUILD_ID").exists():
        raise RuntimeError("Run make app-build first")
    for port in (8000, 3000):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} is already occupied")
    web_root = ROOT / "apps/web"
    standalone = web_root / ".next/standalone"
    if not (standalone / "server.js").is_file():
        raise RuntimeError("Standalone build is missing; run make app-build")
    shutil.copytree(web_root / ".next/static", standalone / ".next/static", dirs_exist_ok=True)
    if (web_root / "public").is_dir():
        shutil.copytree(web_root / "public", standalone / "public", dirs_exist_ok=True)
    commands = {
        "api": [
            str(ROOT / ".venv/bin/python"),
            "-m",
            "uvicorn",
            "risklab.business.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--no-access-log",
        ],
        "worker": [str(ROOT / ".venv/bin/python"), "-m", "risklab.business.worker"],
        "web": ["node", str(standalone / "server.js")],
    }
    processes = {}
    try:
        for name, command in commands.items():
            env = {k: v for k, v in os.environ.items() if not k.startswith("RISKLAB_")}
            env.update(
                MLFLOW_DISABLE_AGENT_HINT="1",
                NEXT_TELEMETRY_DISABLED="1",
                OPENBLAS_NUM_THREADS="1",
                OMP_NUM_THREADS="1",
            )
            env.update(demo_env())
            env.update(database_env("worker" if name == "worker" else "api"))
            env["RISKLAB_API_ORIGIN"] = "http://127.0.0.1:8000"
            if name == "web":
                env.update(HOSTNAME="127.0.0.1", PORT="3000")
            with (RUN / f"{name}.log").open("a") as log:
                process = subprocess.Popen(
                    command,
                    cwd=ROOT / "apps/web" if name == "web" else ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            processes[name] = {"pid": process.pid, "start_ticks": process_identity(process.pid)}
            private_json(state_file, processes)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if not all(alive(record) for record in processes.values()):
                raise RuntimeError("Local service exited; inspect private run logs")
            if http_ready("http://127.0.0.1:8000/api/v1/health/ready") and http_ready(
                "http://localhost:3000"
            ):
                print("RiskLab ready at http://localhost:3000; API http://127.0.0.1:8000")
                return
            time.sleep(0.5)
        raise RuntimeError("Startup timed out; inspect private run logs")
    except BaseException:
        for record in reversed(list(processes.values())):
            stop_process(record)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "start", "stop", "status"))
    args = parser.parse_args()
    RUN.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (RUN / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == "prepare":
            prepare()
        elif args.command == "start":
            start()
        else:
            path = RUN / "processes.json"
            state = json.loads(path.read_text()) if path.exists() else {}
            for name, record in state.items():
                if args.command == "stop":
                    stop_process(record)
                print(f"{name}: {'running' if alive(record) else 'stopped'}")
            if args.command == "stop":
                remaining = {name: record for name, record in state.items() if alive(record)}
                private_json(path, remaining)
                if remaining:
                    raise RuntimeError(
                        "Some local services are still alive; ownership records retained"
                    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # DB/connection exceptions may include credentials, so keep those private.
        message = str(error) if type(error) is RuntimeError else type(error).__name__
        print(f"Local application command failed: {message}", file=sys.stderr)
        raise SystemExit(1) from None
