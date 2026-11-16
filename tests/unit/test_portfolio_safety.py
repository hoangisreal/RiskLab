"""Release hygiene and fold-local preprocessing for the bounded portfolio study."""

import json
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
from scripts.check_public_files import broken_links, candidate_paths, forbidden, push_revisions
from scripts.portfolio_run import aggregate_log_line
from sklearn.compose import ColumnTransformer

from risklab.experiments.e6 import subset_columns, tune_cuda
from risklab.features.application import FeatureColumns
from risklab.models.pipelines import CategoryCaster


@pytest.mark.parametrize(
    "path",
    [
        "AGENTS.md",
        "apps/web/AGENTS.md",
        ".agents/skills/example/SKILL.md",
        ".codex/config.toml",
        ".claude/settings.json",
        ".github/agents/reviewer.md",
        ".github/copilot-instructions.md",
        "data/raw/example.csv",
        "reports/workspaces/study/summary.json",
        ".env.production",
    ],
)
def test_private_paths_are_rejected(path):
    assert forbidden(path)


@pytest.mark.parametrize(
    "path",
    [
        "docs/model_card.md",
        "docs/portfolio/verification.md",
        "src/risklab/cli.py",
        "tests/fixtures/business_v1/import-valid.csv",
        "reports/reference/e1.json",
        ".env.example",
    ],
)
def test_public_application_and_evidence_paths_are_allowed(path):
    assert not forbidden(path)


def test_audit_includes_force_added_ignored_file(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("AGENTS.md\n")
    (tmp_path / "AGENTS.md").write_text("private")
    assert "AGENTS.md" not in candidate_paths(tmp_path)
    subprocess.run(["git", "add", "-f", "AGENTS.md"], cwd=tmp_path, check=True)
    assert "AGENTS.md" in candidate_paths(tmp_path)
    assert forbidden("AGENTS.md")


def test_validation_log_keeps_counts_without_applicant_ids():
    assert aggregate_log_line("12 rows e.g. [123, 456]\n") == "12 rows\n"
    assert aggregate_log_line("accepted ids [123]: known\n") == "accepted ids [redacted]: known\n"


def test_public_links_reject_files_that_exist_only_locally(tmp_path):
    (tmp_path / "README.md").write_text("[private](AGENTS.md) [public](code.py) [dir](src)")
    (tmp_path / "AGENTS.md").write_text("local only")
    (tmp_path / "code.py").write_text("")
    paths = ["README.md", "code.py", "src/example.py"]
    assert broken_links(tmp_path, paths) == ["README.md: unpublished or missing target AGENTS.md"]


def test_push_audit_detects_private_file_deleted_before_tip(tmp_path):
    def git(*args):
        return subprocess.run(
            [
                "git",
                "-c",
                "user.name=Safety Test",
                "-c",
                "user.email=safety@example.invalid",
                *args,
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    git("commit", "--allow-empty", "-qm", "synthetic baseline")
    remote = git("rev-parse", "HEAD")
    (tmp_path / "AGENTS.md").write_text("synthetic private instructions")
    git("add", "AGENTS.md")
    git("commit", "-qm", "synthetic private file")
    git("rm", "AGENTS.md")
    git("commit", "-qm", "synthetic deletion")
    local = git("rev-parse", "HEAD")
    assert not any(forbidden(p) for p in candidate_paths(tmp_path, revision=local))
    revisions = push_revisions(tmp_path, [f"refs/heads/main {local} refs/heads/main {remote}"])
    assert len(revisions) == 2
    assert any(forbidden(p) for rev in revisions for p in candidate_paths(tmp_path, revision=rev))
    new_ref = push_revisions(tmp_path, [f"refs/heads/main {local} refs/heads/main {'0' * 40}"])
    assert len(new_ref) == 3


def test_ablation_removes_exact_columns_without_mutating_contract():
    columns = FeatureColumns(numeric=["a", "b"], categorical=["c"], log=["a"])
    result = subset_columns(columns, {"a", "c"})
    assert result.all == ["b"] and result.log == []
    assert columns.all == ["a", "b", "c"]


def test_cuda_search_fits_categories_inside_train_folds(monkeypatch):
    seen = []

    class FakeBooster:
        def save_config(self):
            return json.dumps({"learner": {"generic_param": {"device": "cuda:0"}}})

    class FakeModel:
        def __init__(self, **kwargs):
            self.params = kwargs
            self.best_iteration = 7
            self.best_score = 0.7 + kwargs["max_depth"] / 100

        def fit(self, X, y, *, eval_set, verbose):
            validation, _ = eval_set[0]
            # Every category identifies a single Train row. Fold validation must be unknown.
            assert X["category"].notna().all()
            assert validation["category"].isna().all()
            seen.append(len(X))

        def get_booster(self):
            return FakeBooster()

    monkeypatch.setattr("risklab.experiments.e6.XGBClassifier", FakeModel)
    X = pd.DataFrame({"category": [str(i) for i in range(20)]})
    y = pd.Series([0, 1] * 10)
    prep = ColumnTransformer(
        [("cat", CategoryCaster(), ["category"])], verbose_feature_names_out=False
    ).set_output(transform="pandas")
    cfg = {
        "folds": 2,
        "seed": 3,
        "depths": [2, 3],
        "child_weights": [10],
        "max_rounds": 10,
        "learning_rate": 0.1,
        "threads": 1,
        "early_stopping_rounds": 2,
    }
    chosen, results = tune_cuda(X, y, prep, cfg)
    assert seen == [10] * 4
    assert len(results) == 2 and chosen.params["max_depth"] == 3
    assert chosen.params["n_estimators"] == 8
    assert not hasattr(prep, "transformers_")


def test_cuda_search_rejects_cpu_fallback(monkeypatch):
    class CpuModel:
        def __init__(self, **kwargs):
            pass

        def fit(self, *args, **kwargs):
            pass

        def get_booster(self):
            return self

        def save_config(self):
            return json.dumps({"learner": {"generic_param": {"device": "cpu"}}})

    monkeypatch.setattr("risklab.experiments.e6.XGBClassifier", CpuModel)
    from sklearn.preprocessing import StandardScaler

    cfg = {
        "folds": 2,
        "seed": 1,
        "depths": [2],
        "child_weights": [10],
        "max_rounds": 10,
        "learning_rate": 0.1,
        "threads": 1,
        "early_stopping_rounds": 2,
    }
    with pytest.raises(RuntimeError, match="fell back"):
        tune_cuda(pd.DataFrame({"a": np.arange(20)}), pd.Series([0, 1] * 10), StandardScaler(), cfg)


@pytest.mark.parametrize(
    "arguments", [["--study", "e7"], ["--study", "e8"], ["--data-root", "unused"]]
)
def test_runner_rejects_ambiguous_inputs_before_creating_outputs(tmp_path, arguments):
    from tests.conftest import REPO_ROOT

    output = tmp_path / "must-not-exist"
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/portfolio_run.py"),
            "--out",
            str(output),
            *arguments,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert not output.exists()
