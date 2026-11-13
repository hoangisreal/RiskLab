"""Exercise Docker's ignore rules with synthetic sentinels, never real secret files."""

import shutil
import subprocess

import pytest

from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.integration


def test_production_secret_paths_are_git_ignored_without_creating_files():
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=REPO_ROOT,
        input="deploy/secrets/prod/api_url\ndeploy/secrets/prod/migrator_url\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "deploy/secrets/prod/api_url",
        "deploy/secrets/prod/migrator_url",
    ]


@pytest.mark.parametrize(
    "ignore_file,excluded",
    [
        (
            ".dockerignore",
            ["deploy/secrets/prod/api_url", ".secrets/local_api_url", ".env.production"],
        ),
        (
            "apps/web/.dockerignore",
            [
                ".env.production",
                ".secrets/token",
                "node_modules/dummy",
                ".next/cache/dummy",
                "test-results/trace.zip",
            ],
        ),
    ],
)
def test_docker_context_excludes_secrets_and_generated_artifacts(tmp_path, ignore_file, excluded):
    if (
        not shutil.which("docker")
        or subprocess.run(["docker", "info"], capture_output=True, check=False).returncode
    ):
        pytest.skip("Docker is required to verify actual build-context filtering")
    context = tmp_path / "context"
    context.mkdir()
    shutil.copyfile(REPO_ROOT / ignore_file, context / ".dockerignore")
    for name in excluded:
        path = context / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic-excluded-sentinel")
    (context / "public-sentinel").write_text("synthetic-public-file")
    output = tmp_path / "export"
    built = subprocess.run(
        [
            "docker",
            "build",
            "--quiet",
            "--file",
            "-",
            "--output",
            f"type=local,dest={output}",
            str(context),
        ],
        input="FROM scratch\nCOPY . /\n",
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert built.returncode == 0, built.stderr
    assert (output / "public-sentinel").read_text() == "synthetic-public-file"
    for name in excluded:
        assert not (output / name).exists(), f"Excluded synthetic path leaked into context: {name}"
