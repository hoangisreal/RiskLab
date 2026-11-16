"""Contract for the one-command Docker demo stack (no Docker daemon needed)."""

from __future__ import annotations

import hashlib

import pytest
import yaml

from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.integration

COMPOSE = REPO_ROOT / "deploy/compose.demo.yml"


def load() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_demo_stack_covers_the_full_workflow():
    services = load()["services"]
    assert set(services) == {"postgres", "demo-init", "api", "worker", "web"}
    assert services["demo-init"]["restart"] == "no"
    for service in ("api", "worker"):
        assert services[service]["depends_on"]["demo-init"]["condition"] == (
            "service_completed_successfully"
        )


def test_demo_stack_publishes_only_loopback_app_ports():
    services = load()["services"]
    assert "ports" not in services["postgres"]
    assert "ports" not in services["worker"]
    for service in ("api", "web"):
        for published in services[service]["ports"]:
            assert published.startswith("127.0.0.1:"), (service, published)


def test_demo_services_have_health_or_completion_gates():
    services = load()["services"]
    assert "healthcheck" in services["postgres"]
    assert "healthcheck" in services["worker"]
    assert services["web"]["depends_on"]["api"]["condition"] == "service_healthy"


def test_demo_catalog_checksum_matches_the_committed_fixture():
    document = load()
    expected = hashlib.sha256(
        (REPO_ROOT / "tests/fixtures/business_v1/catalog.json").read_bytes()
    ).hexdigest()
    assert document["x-bundle-env"]["RISKLAB_CATALOG_SHA256"] == expected


def test_demo_api_worker_read_database_urls_from_files_not_interpolated_urls():
    document = load()
    for service, filename in (("api", "api_url"), ("worker", "worker_url")):
        env = document["services"][service]["environment"]
        assert env["RISKLAB_DATABASE_URL_FILE"] == f"/demo-secrets/{filename}"
        assert "RISKLAB_DATABASE_URL" not in env
    raw = COMPOSE.read_text()
    assert "risklab_api_login:" not in raw and "risklab_worker_login:" not in raw


def test_demo_secrets_volumes_are_isolated_per_service():
    services = load()["services"]
    api_volumes = " ".join(services["api"]["volumes"])
    worker_volumes = " ".join(services["worker"]["volumes"])
    assert "demo_secrets_api" in api_volumes and "demo_secrets_worker" not in api_volumes
    assert "demo_secrets_worker" in worker_volumes and "demo_secrets_api" not in worker_volumes


def test_demo_worker_healthcheck_requires_a_ready_scorer():
    command = " ".join(load()["services"]["worker"]["healthcheck"]["test"])
    assert "scorer_ready" in command


def test_demo_init_refuses_production_and_prod_compose_has_no_demo_passwords():
    init_source = (REPO_ROOT / "scripts/demo_compose_init.py").read_text()
    assert "RISKLAB_ENVIRONMENT" in init_source and "production" in init_source
    prod = (REPO_ROOT / "deploy/compose.prod.yml").read_text()
    assert "RiskLabDemo" not in prod and "risklab-demo" not in prod
