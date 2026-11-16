"""Smoke the Docker demo stack through its public HTTP surface only.

Polls web + API readiness, logs in as the demo Manager, imports the synthetic
CSV under a unique idempotency key and waits for that batch's own scoring to
succeed, so stale results or a worker without a scorer fail the smoke.
Exits non-zero on the first failed expectation; prints no credentials.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import time
import urllib.request
import uuid
from pathlib import Path

WEB = os.environ.get("RISKLAB_DEMO_WEB_URL", "http://localhost:3000")
API = os.environ.get("RISKLAB_DEMO_API_URL", "http://127.0.0.1:8000")
MANAGER_PASSWORD = os.environ.get("RISKLAB_DEMO_MANAGER_PASSWORD", "RiskLabDemo-Manager1")


def get(opener: urllib.request.OpenerDirector, url: str) -> tuple[int, dict]:
    with opener.open(url, timeout=5) as response:
        return response.status, json.loads(response.read() or b"{}")


def main() -> None:
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    deadline = time.monotonic() + 180
    while True:
        try:
            api_status, _ = get(opener, f"{API}/api/v1/health/ready")
            with opener.open(WEB, timeout=5) as response:
                web_status = response.status
            if api_status == 200 and web_status == 200:
                break
        except Exception:
            pass
        if time.monotonic() > deadline:
            raise SystemExit("Demo stack did not become ready in 180s")
        time.sleep(3)
    # Login is itself a mutation: bootstrap an anonymous CSRF session first.
    _, bootstrap = get(opener, f"{WEB}/api/v1/auth/csrf")
    payload = json.dumps({"username": "demo.manager", "password": MANAGER_PASSWORD}).encode()
    request = urllib.request.Request(
        f"{WEB}/api/v1/auth/login",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Origin": WEB,
            "X-CSRF-Token": bootstrap["data"]["csrf_token"],
        },
    )
    with opener.open(request, timeout=5) as response:
        assert response.status == 200, response.status
        csrf_token = json.loads(response.read())["data"]["csrf_token"]
    _, me = get(opener, f"{WEB}/api/v1/auth/me")
    assert me["data"]["username"] == "demo.manager" and me["data"]["role"] == "MANAGER", me

    # Import-to-scoring gate: a fixed key makes repeat smokes idempotent.
    fixture = Path(__file__).parent.parent / "tests/fixtures/business_v1/import-valid.csv"
    csv_bytes = fixture.read_bytes()
    boundary = "----risklab-demo-smoke"

    def field(name: str, value: str) -> bytes:
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
        return head.encode() + value.encode() + b"\r\n"

    file_head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="import.csv"\r\n'
        "Content-Type: text/csv\r\n\r\n"
    ).encode()
    parts = [
        field("import_contract_version", "demo-import/1"),
        field("catalog_version", "synthetic-b00-v1"),
        file_head + csv_bytes + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    request = urllib.request.Request(
        f"{WEB}/api/v1/imports",
        data=b"".join(parts),
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Origin": WEB,
            "X-CSRF-Token": csrf_token,
            # Unique per run: a reused key could replay an old import and
            # let stale scored applications mask a dead worker.
            "Idempotency-Key": f"docker-demo-smoke-{uuid.uuid4().hex}",
        },
    )
    with opener.open(request, timeout=10) as response:
        assert response.status == 202, response.status
        import_id = json.loads(response.read())["data"]["id"]
    deadline = time.monotonic() + 120
    while True:
        _, batch = get(opener, f"{WEB}/api/v1/imports/{import_id}")
        if batch["data"]["state"] == "SUCCEEDED":
            break
        if batch["data"]["state"] == "FAILED" or time.monotonic() > deadline:
            raise SystemExit(f"Smoke import did not succeed: {batch['data']['state']}")
        time.sleep(2)
    # Wait for this batch's own scoring to settle, not any historical result.
    while True:
        _, page = get(opener, f"{WEB}/api/v1/applications?page_size=100")
        mine = [a for a in page["data"] if a["import_id"] == import_id]
        pending = [a for a in mine if a["scoring_state"] in {"QUEUED", "RUNNING"}]
        failed = [a for a in mine if a["scoring_state"] == "FAILED"]
        scored = [a for a in mine if a["scoring_state"] == "SUCCEEDED"]
        if failed:
            raise SystemExit(f"Smoke scoring failed for this import: {failed}")
        if mine and not pending and scored:
            break
        if time.monotonic() > deadline:
            raise SystemExit(f"Smoke scoring did not settle for this import: {mine}")
        time.sleep(2)
    print(
        "Docker demo smoke passed: web 200, API ready, Manager login, "
        f"import SUCCEEDED and {len(scored)} of its application(s) scored."
    )


if __name__ == "__main__":
    main()
