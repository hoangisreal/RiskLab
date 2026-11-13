"""Local browser-test backend with disposable PostgreSQL and synthetic test accounts only.

Never use this entrypoint for deployment: it seeds test-only credentials and
deliberately isolates its database instead of consuming an application DB URL.
It also fits a small synthetic model and runs the real worker loop so browser
tests exercise import -> scoring -> review -> decision, never a mocked score.
"""

from __future__ import annotations

import signal
import threading
import time
from pathlib import Path

import uvicorn
from scripts.synthetic_application import build_scorer
from tests.business.conftest import disposable_database, disposable_postgres

from risklab.business.db import session_factory
from risklab.business.identity import HASHER
from risklab.business.main import create_app
from risklab.business.models import User
from risklab.business.settings import Settings
from risklab.business.worker import WorkerRuntime, run_once


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
                        if not run_once(database.worker, runtime, worker_id="browser-worker"):
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
