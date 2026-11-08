"""RiskLab command line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from risklab import __version__
from risklab.config import (
    CONFIG_DIR_ENV,
    ConfigError,
    load_data_config,
    load_model,
    load_partition_config,
    load_raw_schema,
    project_root,
    resolve_config_dir,
)
from risklab.data.ingest import IngestError, run_ingest
from risklab.data.loaders import LoaderError
from risklab.data.partition import PartitionError, run_partition
from risklab.data.validate import (
    ApplicationDomains,
    ValidateError,
    ValidationRules,
    run_validate,
)
from risklab.data.validate_history import HistoryValidationError, validate_history
from risklab.experiments.diagnostics import run_diagnostics
from risklab.experiments.e1 import E1Error, run_e1
from risklab.experiments.e234 import run_e2, run_e3, run_e4
from risklab.features.build import FeatureBuildError, build_features
from risklab.features.registry import load_registry
from risklab.manifest import write_json_atomic
from risklab.models.evaluate import EvaluationError
from risklab.models.final_eval import FinalEvalError, FinalEvaluationConfig, run_final_eval
from risklab.models.select import SelectionError, create_lock
from risklab.synthetic import generate
from risklab.tracking import TrackingError
from risklab.verify import (
    VerificationConfig,
    compare_feature_tables,
    snapshot_reference,
    verify_reference,
)

app = typer.Typer(help="RiskLab: credit risk prediction and monitoring demo.", no_args_is_help=True)


@app.callback()
def main(
    ctx: typer.Context,
    config_dir: Annotated[
        Path | None,
        typer.Option(
            "--config-dir",
            envvar=CONFIG_DIR_ENV,
            help="Directory containing the YAML configs (default: ./configs).",
        ),
    ] = None,
) -> None:
    ctx.obj = {"config_dir": config_dir}


@app.command()
def version() -> None:
    """Print the RiskLab version."""
    typer.echo(__version__)


def _config_dir(ctx: typer.Context) -> Path:
    return resolve_config_dir(ctx.obj["config_dir"])


def _fail(message: str) -> NoReturn:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1)


@app.command()
def ingest(
    ctx: typer.Context,
    table: Annotated[
        list[str] | None,
        typer.Option("--table", help="Table to ingest (repeatable). Default: all tables."),
    ] = None,
) -> None:
    """Convert raw CSVs to Parquet and write the data manifests."""
    try:
        cfg_dir = _config_dir(ctx)
        cfg = load_data_config(cfg_dir)
        schema = load_raw_schema(cfg_dir, data_cfg=cfg)
        manifest = run_ingest(cfg, schema, root=project_root(cfg_dir), tables=table, log=typer.echo)
    except (ConfigError, IngestError) as exc:
        _fail(str(exc))
    summary = manifest["summary"]
    typer.echo(
        f"Ingest done in {summary['wall_seconds']}s, peak RSS "
        f"{summary['peak_rss_bytes'] / 2**20:.0f} MiB. Manifest: {cfg.manifest_path}"
    )


@app.command()
def partition(
    ctx: typer.Context,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help="Replace existing partitions with a different hash (invalidates downstream).",
        ),
    ] = False,
) -> None:
    """Assign stratified Train/Calibration/Selection/Test/Monitoring partitions."""
    try:
        cfg_dir = _config_dir(ctx)
        data_cfg = load_data_config(cfg_dir)
        cfg = load_partition_config(cfg_dir)
        result = run_partition(
            cfg, data_cfg, root=project_root(cfg_dir), overwrite=overwrite, log=typer.echo
        )
    except (ConfigError, PartitionError) as exc:
        _fail(str(exc))
    doc = result["document"]
    for role, stats in doc["roles"].items():
        typer.echo(
            f"{role}: {stats['rows']:,} rows, {stats['positives']:,} positives "
            f"({stats['positive_rate']:.5f})"
        )
    typer.echo(
        f"Partitions {result['status']}: {doc['rows']:,} rows, prevalence "
        f"{doc['positive_rate']:.5f}, partition_hash {doc['partition_hash']}"
    )


@app.command()
def validate(ctx: typer.Context) -> None:
    """Validate application_train and application_test; exit 1 if any error check fails."""
    try:
        cfg_dir = _config_dir(ctx)
        data_cfg = load_data_config(cfg_dir)
        schema = load_raw_schema(cfg_dir, data_cfg=data_cfg)
        rules = load_model(cfg_dir, "application_validation.yaml", ValidationRules)
        domains = load_model(cfg_dir, "application_domains.yaml", ApplicationDomains)
        doc = run_validate(
            data_cfg, schema, rules, domains, root=project_root(cfg_dir), log=typer.echo
        )
    except (ConfigError, ValidateError) as exc:
        _fail(str(exc))
    for c in doc["checks"]:
        acc = c.get("accepted_exceptions")
        if c["severity"] == "info" or c["offending_rows"] > 0 or acc:
            flag = "FAIL" if c["severity"] == "error" and c["offending_rows"] > 0 else c["severity"]
            if acc and flag == "error":
                flag = "accepted"
            examples = f" e.g. {c['examples']}" if c["examples"] else ""
            typer.echo(f"[{flag}] {c['table']} {c['name']}: {c['offending_rows']:,} rows{examples}")
            if acc:
                typer.echo(
                    f"    accepted exception: {acc['count']:,} row(s), ids {acc['ids']}: "
                    f"{acc['reason']}"
                )
            top = c.get("details", {}).get("top")
            if top:
                typer.echo("    " + ", ".join(f"{t['column']} {t['null_rate']:.3f}" for t in top))
    s = doc["summary"]
    typer.echo(
        f"Validation {doc['status']}: {s['checks']} checks, {s['errors']} error(s), "
        f"{s['warnings']} warning(s)"
    )
    if s["errors"]:
        raise typer.Exit(code=1)


@app.command()
def synthetic(
    ctx: typer.Context,
    out: Annotated[Path | None, typer.Option("--out", help="Output directory.")] = None,
    n_applicants: Annotated[
        int | None, typer.Option("--n-applicants", min=50, help="Applicants (train + test).")
    ] = None,
    seed: Annotated[int | None, typer.Option("--seed", help="Random seed.")] = None,
) -> None:
    """Write the synthetic raw dataset (eight CSVs matching raw_schema.yaml)."""
    try:
        cfg_dir = _config_dir(ctx)
        cfg = load_data_config(cfg_dir)
        schema = load_raw_schema(cfg_dir, data_cfg=cfg)
    except ConfigError as exc:
        _fail(str(exc))
    syn = cfg.synthetic
    target = out or syn.out_dir
    counts = generate(
        target,
        schema,
        {t: f for f, t in cfg.tables.items()},
        n_applicants=n_applicants or syn.n_applicants,
        seed=syn.seed if seed is None else seed,
    )
    for name, rows in counts.items():
        typer.echo(f"{name}: {rows:,} rows")
    typer.echo(f"Wrote synthetic CSVs to {target}")


@app.command()
def e1(
    ctx: typer.Context,
    no_tracking: Annotated[
        bool,
        typer.Option("--no-tracking", help="Skip MLflow; metrics are not reportable."),
    ] = False,
    served: Annotated[
        bool, typer.Option("--served", help="Repeat E1 on E2's selected source set.")
    ] = False,
) -> None:
    """Run E1 (Logistic Regression vs LightGBM, application features) on Train and Selection."""
    try:
        cfg_dir = _config_dir(ctx)
        data_cfg = load_data_config(cfg_dir)
        groups = None
        if served:
            import json

            decision = json.loads(
                (data_cfg.reports_dir / "experiments/e2/summary.json").read_text()
            )
            if decision.get("contract_version") != load_registry(cfg_dir).contract_version:
                raise E1Error("run E2 for the current feature contract before E1 --served")
            groups = [g for g in decision["served_groups"] if g != "application"]
        summary = run_e1(
            config_dir=cfg_dir,
            root=project_root(cfg_dir),
            output_dir=data_cfg.reports_dir / "experiments" / ("e1_served" if served else "e1"),
            served_groups=groups,
            data_cfg=data_cfg,
            tracking=not no_tracking,
            log=typer.echo,
        )
    except (
        ConfigError,
        E1Error,
        LoaderError,
        PartitionError,
        EvaluationError,
        TrackingError,
    ) as exc:
        _fail(str(exc))
    for name, m in summary["selection_metrics"].items():
        typer.echo(f"{name}: " + ", ".join(f"{k}={v:.5f}" for k, v in m.items()))
    d = summary["decision"]
    typer.echo(f"Decision: {d['preferred']} ({d['reason']})")
    if summary["run_ids"]:
        typer.echo(f"MLflow runs: {summary['run_ids']}")


@app.command()
def e5(
    ctx: typer.Context,
    no_tracking: Annotated[
        bool,
        typer.Option("--no-tracking", help="Skip MLflow; metrics are not reportable."),
    ] = False,
) -> None:
    """Run E5 (post-lock challenger study: XGBoost/CatBoost/HistGB vs champion)."""
    from risklab.experiments.e5 import E5Error, run_e5

    try:
        cfg_dir = _config_dir(ctx)
        data_cfg = load_data_config(cfg_dir)
        root = project_root(cfg_dir)
        summary = run_e5(
            config_path=root / "experiments/e5.yaml",
            config_dir=cfg_dir,
            root=root,
            output_dir=data_cfg.reports_dir / "experiments/e5",
            data_cfg=data_cfg,
            tracking=not no_tracking,
            log=typer.echo,
        )
    except (
        ConfigError,
        E5Error,
        LoaderError,
        PartitionError,
        EvaluationError,
        TrackingError,
    ) as exc:
        _fail(str(exc))
    for name, m in summary["selection_metrics"].items():
        typer.echo(f"{name}: " + ", ".join(f"{k}={v:.5f}" for k, v in m.items()))
    for name, c in summary["comparisons_vs_champion"].items():
        primary = c["paired_bootstrap_vs_champion"]["roc_auc"]
        typer.echo(
            f"{name} vs champion: delta roc_auc={primary['delta']:+.5f} "
            f"CI [{primary['ci_low']:+.5f}, {primary['ci_high']:+.5f}] "
            f"flagged_superior={c['flagged_superior']}"
        )
    if summary["run_ids"]:
        typer.echo(f"MLflow runs: {summary['run_ids']}")


@app.command()
def diagnostics(
    ctx: typer.Context,
    no_tracking: Annotated[
        bool, typer.Option("--no-tracking", help="Skip MLflow (results are then not reportable).")
    ] = False,
    served: Annotated[
        bool, typer.Option("--served", help="Check the E2 served feature set.")
    ] = False,
) -> None:
    """Run the leakage diagnostics (label permutation, single-feature screening) on Train."""
    try:
        cfg_dir = _config_dir(ctx)
        data_cfg = load_data_config(cfg_dir)
        groups = None
        if served:
            import json

            e2 = json.loads((data_cfg.reports_dir / "experiments/e2/summary.json").read_text())
            groups = [g for g in e2["served_groups"] if g != "application"]
        report = run_diagnostics(
            config_dir=cfg_dir,
            root=project_root(cfg_dir),
            output_dir=data_cfg.reports_dir / "diagnostics",
            data_cfg=data_cfg,
            tracking=not no_tracking,
            log=typer.echo,
            served_groups=groups,
        )
    except (ConfigError, LoaderError, PartitionError, TrackingError) as exc:
        _fail(str(exc))
    typer.echo(f"Diagnostics {'passed' if report['passed'] else 'NEED REVIEW'}")
    if report["run_id"]:
        typer.echo(f"MLflow run: {report['run_id']}")
    if not report["passed"]:
        raise typer.Exit(code=1)


@app.command("validate-history")
def validate_history_cmd(ctx: typer.Context) -> None:
    """Validate historical tables (FK coverage, temporal validity, duplicate keys)."""
    try:
        cfg_dir = _config_dir(ctx)
        data_cfg = load_data_config(cfg_dir)
        report = validate_history(data_cfg, config_dir=cfg_dir)
    except (ConfigError, HistoryValidationError) as exc:
        _fail(str(exc))
    for name, value in report["counts"].items():
        typer.echo(f"{name}: {value}")
    typer.echo(f"Historical validation: {report['status']}; exclusions={report['excluded_rows']}")
    if not report["passed"]:
        _fail(f"historical key conflicts: {report['errors']}")


@app.command()
def features(ctx: typer.Context) -> None:
    """Build historical feature groups for all Train applicants (batch SQL)."""
    try:
        cfg_dir = _config_dir(ctx)
        data_cfg = load_data_config(cfg_dir)
        partition_cfg = load_partition_config(cfg_dir)
        from risklab.data.clean import clean_application, load_cleaning_config
        from risklab.data.loaders import PartitionRole, Purpose, load_partition

        schema = load_raw_schema(cfg_dir, data_cfg=data_cfg)
        cleaning = load_cleaning_config(cfg_dir, schema)
        table = load_partition(
            PartitionRole.train,
            Purpose.fit,
            data_cfg=data_cfg,
            partition_cfg=partition_cfg,
            config_dir=cfg_dir,
        )
        cleaned = clean_application(table, cleaning)
        registry = load_registry(cfg_dir)
        out = data_cfg.reports_dir.parent / "features"
        build_features(
            data_cfg=data_cfg,
            root=project_root(cfg_dir),
            applicant_ids=cleaned[partition_cfg.id_column].to_pylist(),
            application_cleaned=cleaned,
            registry=registry,
            output_dir=out,
            log=typer.echo,
        )
    except (ConfigError, LoaderError, PartitionError, FeatureBuildError) as exc:
        _fail(str(exc))
    typer.echo(f"Feature groups written to {out}")


@app.command()
def e2(ctx: typer.Context) -> None:
    """Run E2 (feature-group ablation) on Train/Selection."""
    try:
        cfg_dir = _config_dir(ctx)
        summary = run_e2(config_dir=cfg_dir, root=project_root(cfg_dir), log=typer.echo)
        data_cfg = load_data_config(cfg_dir)
        write_json_atomic(data_cfg.reports_dir / "experiments" / "e2" / "summary.json", summary)
    except Exception as exc:
        _fail(str(exc))
    typer.echo(f"E2 served groups: {summary['served_groups']}")


@app.command()
def e3(ctx: typer.Context) -> None:
    """Run E3 (calibration comparison) on Calibration/Selection."""
    try:
        cfg_dir = _config_dir(ctx)
        import json as _json

        e2_path = load_data_config(cfg_dir).reports_dir / "experiments" / "e2" / "summary.json"
        groups = None
        if e2_path.is_file():
            served = _json.loads(e2_path.read_text(encoding="utf-8"))["served_groups"]
            groups = [g for g in served if g != "application"]
        summary = run_e3(
            config_dir=cfg_dir, root=project_root(cfg_dir), groups=groups, log=typer.echo
        )
        data_cfg = load_data_config(cfg_dir)
        write_json_atomic(data_cfg.reports_dir / "experiments" / "e3" / "summary.json", summary)
    except Exception as exc:
        _fail(str(exc))
    typer.echo(f"E3 chosen calibration: {summary['selection']['chosen']}")


@app.command()
def e4(ctx: typer.Context) -> None:
    """Run E4 (threshold and cost sensitivity) on Selection."""
    try:
        cfg_dir = _config_dir(ctx)
        import json as _json

        e2_path = load_data_config(cfg_dir).reports_dir / "experiments" / "e2" / "summary.json"
        groups = None
        if e2_path.is_file():
            served = _json.loads(e2_path.read_text(encoding="utf-8"))["served_groups"]
            groups = [g for g in served if g != "application"]
        e3_path = load_data_config(cfg_dir).reports_dir / "experiments" / "e3" / "summary.json"
        method = "sigmoid"
        if e3_path.is_file():
            method = _json.loads(e3_path.read_text(encoding="utf-8"))["selection"]["chosen"]
        summary = run_e4(
            config_dir=cfg_dir,
            root=project_root(cfg_dir),
            groups=groups,
            calibration_method=method,
            log=typer.echo,
        )
        data_cfg = load_data_config(cfg_dir)
        write_json_atomic(data_cfg.reports_dir / "experiments" / "e4" / "summary.json", summary)
    except Exception as exc:
        _fail(str(exc))
    typer.echo(f"E4 threshold: {summary['threshold_choice']['threshold']:.4f}")


@app.command("lock-selection")
def lock_selection(
    ctx: typer.Context,
    candidate: Annotated[
        Path, typer.Option("--candidate", help="Validated candidate.json from prepare-candidate.")
    ],
) -> None:
    """Freeze a parity-verified Selection artifact (clean tree required)."""
    import json

    from risklab.data.loaders import partition_info
    from risklab.manifest import library_versions, sha256_file
    from risklab.models.select import protocol_hashes
    from risklab.models.threshold import load_cost_config

    try:
        cfg_dir = _config_dir(ctx)
        data_cfg = load_data_config(cfg_dir)
        partition_cfg = load_partition_config(cfg_dir)
        metadata = json.loads(candidate.read_text(encoding="utf-8"))
        if (
            metadata.get("protocol_hashes") != protocol_hashes(project_root(cfg_dir))
            or metadata.get("library_versions") != library_versions()
            or metadata.get("contract_version") != load_registry(cfg_dir).contract_version
        ):
            raise SelectionError("candidate context changed since parity/Selection verification")
        model_path = Path(metadata["model_path"])
        if sha256_file(model_path) != metadata["model_sha256"]:
            raise SelectionError("candidate artifact changed")
        info = partition_info(
            "train", data_cfg=data_cfg, partition_cfg=partition_cfg, config_dir=cfg_dir
        )
        if metadata["partition_hash"] != info.partition_hash or metadata[
            "data_manifest_hash"
        ] != sha256_file(data_cfg.manifest_path):
            raise SelectionError("candidate data changed since parity/Selection verification")
        lock = create_lock(
            locks_dir=data_cfg.reports_dir / "locks",
            model_run_id=metadata["selection_run_id"],
            feature_set="selected",
            feature_groups=metadata["feature_groups"],
            contract_version=load_registry(cfg_dir).contract_version,
            calibration_method=metadata["calibration_method"],
            threshold=metadata["threshold"],
            config={
                "candidate": metadata,
                "cost": load_cost_config(cfg_dir).model_dump(),
                "evaluation": load_model(
                    cfg_dir, "final_eval.yaml", FinalEvaluationConfig
                ).model_dump(),
            },
            partition_hash=info.partition_hash,
            data_manifest_hash=sha256_file(data_cfg.manifest_path),
            model_path=model_path,
            root=project_root(cfg_dir),
        )
    except (ConfigError, LoaderError, SelectionError, ValueError, KeyError, OSError) as exc:
        _fail(str(exc))
    typer.echo(f"Selection locked: {lock['lock_id']}")


@app.command("policy-analysis")
def policy_analysis(ctx: typer.Context) -> None:
    """Compare gender/age policies on Selection without changing the default policy."""
    import json

    from risklab.experiments.e234 import run_policy_analysis

    try:
        cfg_dir = _config_dir(ctx)
        data = load_data_config(cfg_dir)
        e2 = json.loads((data.reports_dir / "experiments/e2/summary.json").read_text())
        summary = run_policy_analysis(
            config_dir=cfg_dir,
            root=project_root(cfg_dir),
            groups=[g for g in e2["served_groups"] if g != "application"],
        )
        write_json_atomic(data.reports_dir / "experiments/policy/summary.json", summary)
    except Exception as exc:
        _fail(str(exc))
    typer.echo(f"Policy remains {summary['policy']}; MLflow run {summary['run_id']}")


@app.command("prepare-candidate")
def prepare_candidate_cmd(
    ctx: typer.Context,
    model_path: Annotated[
        Path | None, typer.Option("--model-path", help="Existing artifact for legacy E3 runs.")
    ] = None,
) -> None:
    """Verify the selected artifact and complete feature/prediction parity; never refit."""
    from risklab.models.candidate import prepare_candidate

    try:
        cfg_dir = _config_dir(ctx)
        candidate = prepare_candidate(
            root=project_root(cfg_dir), config_dir=cfg_dir, model_path=model_path
        )
    except Exception as exc:
        _fail(str(exc))
    typer.echo(
        f"Candidate verified ({candidate['parity']['rows']} applicants): "
        f"{load_data_config(cfg_dir).reports_dir / 'models/candidate.json'}"
    )


@app.command("final-eval")
def final_eval(
    ctx: typer.Context,
    lock: Annotated[str, typer.Option("--lock")],
    acknowledge_test_reuse: Annotated[str | None, typer.Option("--acknowledge-test-reuse")] = None,
) -> None:
    """Evaluate the locked model on Test (once per lock)."""
    try:
        cfg_dir = _config_dir(ctx)
        data_cfg = load_data_config(cfg_dir)
        partition_cfg = load_partition_config(cfg_dir)
        result = run_final_eval(
            lock_id=lock,
            locks_dir=data_cfg.reports_dir / "locks",
            reports_dir=data_cfg.reports_dir,
            root=project_root(cfg_dir),
            data_cfg=data_cfg,
            partition_cfg=partition_cfg,
            config_dir=cfg_dir,
            acknowledge_test_reuse=acknowledge_test_reuse,
            tracking=True,
        )
    except (ConfigError, FinalEvalError) as exc:
        _fail(str(exc))
    typer.echo(
        f"Final eval {result['lock_id']}: {result['metrics']} "
        f"(Test openings: {result['test_openings_total']})"
    )


@app.command()
def verify(
    ctx: typer.Context,
    reference: Annotated[Path, typer.Option("--reference")] = Path(
        "reports/reference/contract-1.1"
    ),
    feature_reference: Annotated[Path | None, typer.Option("--feature-reference")] = None,
) -> None:
    """Compare fresh manifests against the committed reference directory."""
    try:
        cfg_dir = _config_dir(ctx)
        data_cfg = load_data_config(cfg_dir)
        cfg = load_model(cfg_dir, "verification.yaml", VerificationConfig)
        report = verify_reference(reference, data_cfg.reports_dir, metric_atol=cfg.metric_atol)
        if feature_reference is not None:
            passed = compare_feature_tables(
                feature_reference, data_cfg.reports_dir.parent / "features", rtol=cfg.feature_rtol
            )
            report["checks"].append(
                {"file": "feature_tables", "status": "ok" if passed else "mismatch"}
            )
            report["passed"] = report["passed"] and passed
    except ConfigError as exc:
        _fail(str(exc))
    typer.echo(f"Verify {'passed' if report['passed'] else 'FAILED'}: {report['checks']}")
    if not report["passed"]:
        raise typer.Exit(code=1)


def _audit_path(ctx: typer.Context) -> Path:
    return load_data_config(_config_dir(ctx)).reports_dir / "registry_audit.jsonl"


registry_app = typer.Typer(help="MLflow registry alias management.")
app.add_typer(registry_app, name="registry")


@registry_app.command("status")
def registry_status() -> None:
    """Show the champion alias resolution."""
    from risklab.registry.client import RegistryError, resolve_champion

    try:
        info = resolve_champion()
    except RegistryError as exc:
        _fail(str(exc))
    typer.echo(str(info))


@registry_app.command("promote")
def registry_promote(
    ctx: typer.Context,
    version: Annotated[str, typer.Option("--version")],
    reason: Annotated[str, typer.Option("--reason")] = "",
    force: Annotated[
        bool, typer.Option("--force", help="Bypass metric gate for an audited demo.")
    ] = False,
) -> None:
    """Move @champion to a version and record the audit entry."""
    from risklab.models.select import protocol_hashes
    from risklab.registry.client import (
        RegistryError,
        champion_info_or_none,
        check_promotion,
        set_alias,
    )
    from risklab.registry.gates import GateConfig

    try:
        if force and not reason.strip():
            raise RegistryError("--force requires a non-empty --reason")
        gate = check_promotion(
            version,
            expected_contract=load_registry(_config_dir(ctx)).contract_version,
            config=load_model(_config_dir(ctx), "registry.yaml", GateConfig).model_dump(),
            expected_protocol=protocol_hashes(project_root(_config_dir(ctx))),
        )
        if not gate["passed"] and not force:
            raise RegistryError(f"promotion gate blocked candidate: {gate['checks']}")
        current = champion_info_or_none()
        previous = current["version"] if current is not None else None
        if "champion_version" in gate and previous != gate["champion_version"]:
            raise RegistryError("champion changed during gate evaluation; retry promotion")
        set_alias(version)
    except RegistryError as exc:
        _fail(str(exc))
    from risklab.registry.audit import append_audit

    append_audit(
        _audit_path(ctx),
        action="promote",
        from_version=previous,
        to_version=version,
        actor="cli",
        reason=f"FORCE: {reason}" if force else reason,
    )
    typer.echo(f"Champion -> version {version}")


@registry_app.command("rollback")
def registry_rollback(
    ctx: typer.Context,
    version: Annotated[str, typer.Option("--version")],
    reason: Annotated[str, typer.Option("--reason")] = "",
) -> None:
    """Roll @champion back to a previous version (audit-logged)."""
    from risklab.api.model_store import ModelStore, load_from_registry
    from risklab.registry.audit import read_audit
    from risklab.registry.bundle import validate_serving_config
    from risklab.registry.client import (
        RegistryError,
        read_serving_config,
        resolve_champion,
        resolve_version,
        set_alias,
    )

    try:
        history = read_audit(_audit_path(ctx))
        if not any(
            e.get("to_version") == version and e.get("action") == "promote" for e in history
        ):
            raise RegistryError("rollback target must have an audited prior promotion")
        candidate = read_serving_config(resolve_version(version))
        validate_serving_config(
            candidate, expected_contract=load_registry(_config_dir(ctx)).contract_version
        )
        from risklab.models.select import protocol_hashes

        load_from_registry(
            ModelStore(),
            version=version,
            expected_contract=load_registry(_config_dir(ctx)).contract_version,
            expected_protocol=protocol_hashes(project_root(_config_dir(ctx))),
        )
        try:
            previous = resolve_champion()["version"]
        except RegistryError:
            previous = None
        set_alias(version)
    except (RegistryError, ValueError) as exc:
        _fail(str(exc))
    from risklab.registry.audit import append_audit

    append_audit(
        _audit_path(ctx),
        action="rollback",
        from_version=previous,
        to_version=version,
        actor="cli",
        reason=reason,
    )
    typer.echo(f"Champion rolled back -> version {version}")


@app.command("snapshot-reference")
def snapshot_reference_cmd(
    ctx: typer.Context,
    out: Annotated[Path, typer.Option("--out")] = Path("reports/reference/contract-1.1"),
) -> None:
    """Export aggregate references from an explicitly chosen baseline run."""
    try:
        files = snapshot_reference(load_data_config(_config_dir(ctx)).reports_dir, out)
    except (OSError, ValueError, KeyError) as exc:
        _fail(str(exc))
    typer.echo(f"Wrote {len(files)} aggregate reference files to {out}")


monitoring_app = typer.Typer(help="Register and evaluate seeded Monitoring-pool batches.")
app.add_typer(monitoring_app, name="monitoring")


@monitoring_app.command("create")
def monitoring_create(
    ctx: typer.Context,
    batch_id: Annotated[str, typer.Option("--batch-id")],
    scenario: Annotated[str, typer.Option("--scenario")] = "control",
    seed: Annotated[int | None, typer.Option("--seed")] = None,
    rows: Annotated[int | None, typer.Option("--rows")] = None,
) -> None:
    from risklab.monitoring.service import BatchSpec, MonitoringConfig
    from risklab.monitoring.simulate import register_batch

    try:
        cfg_dir = _config_dir(ctx)
        cfg = load_model(cfg_dir, "monitoring.yaml", MonitoringConfig)
        spec = BatchSpec.model_validate(
            dict(
                batch_id=batch_id,
                scenario=scenario,
                seed=cfg.seed if seed is None else seed,
                rows=cfg.batch_size if rows is None else rows,
            )
        )
        register_batch(project_root(cfg_dir) / "data/monitoring/manifest.json", **spec.model_dump())
    except (ConfigError, ValueError) as exc:
        _fail(str(exc))
    typer.echo(f"Registered batch {batch_id}")


@monitoring_app.command("evaluate")
def monitoring_evaluate(
    ctx: typer.Context,
    batch_id: Annotated[str, typer.Option("--batch-id")],
    version: Annotated[str | None, typer.Option("--version")] = None,
) -> None:
    from risklab.api.model_store import ModelStore, load_from_registry
    from risklab.monitoring.service import BatchSpec, registered_batches, run_monitoring

    try:
        cfg_dir = _config_dir(ctx)
        root = project_root(cfg_dir)
        spec = BatchSpec.model_validate(registered_batches(root)[batch_id])
        store = ModelStore()
        load_from_registry(
            store, version=version, expected_contract=load_registry(cfg_dir).contract_version
        )
        result = run_monitoring(
            spec, store=store, config_dir=cfg_dir, root=root, model_version=version
        )
    except Exception as exc:
        _fail(str(exc))
    typer.echo(f"Batch {batch_id}: alert={result['alert']}, model={result['model_version']}")


@monitoring_app.command("calibrate")
def monitoring_calibrate(
    ctx: typer.Context,
    candidate: Annotated[Path | None, typer.Option("--candidate")] = None,
    version: Annotated[str | None, typer.Option("--version")] = None,
) -> None:
    """Freeze alert thresholds on calibration controls and measure separate held-out controls."""
    from risklab.api.model_store import ModelStore, load_from_registry
    from risklab.models.select import protocol_hashes
    from risklab.monitoring.policy import calibrate_controls, load_candidate
    from risklab.monitoring.service import MonitoringConfig

    try:
        cfg_dir = _config_dir(ctx)
        root = project_root(cfg_dir)
        if (candidate is None) == (version is None):
            raise ValueError("choose exactly one of --candidate and --version")
        loaded, profile = load_candidate(candidate) if candidate else (None, None)
        if version is not None:
            loaded = load_from_registry(
                ModelStore(),
                version=version,
                expected_contract=load_registry(cfg_dir).contract_version,
                expected_protocol=protocol_hashes(root),
            )
            profile = loaded.reference_profile
        if loaded is None or profile is None:
            raise ValueError("model reference profile is missing")
        report = calibrate_controls(
            loaded,
            profile,
            config=load_model(cfg_dir, "monitoring.yaml", MonitoringConfig),
            config_dir=cfg_dir,
            root=root,
            reports_dir=load_data_config(cfg_dir).reports_dir,
        )
    except Exception as exc:
        _fail(str(exc))
    typer.echo(
        f"Held-out control false-alarm rate: {report['validation_false_alarm_rate']:.1%}; "
        f"MLflow run {report['run_id']}"
    )
    if not report["passed"]:
        raise typer.Exit(code=1)


@registry_app.command("register")
def registry_register(ctx: typer.Context, lock: Annotated[str, typer.Option("--lock")]) -> None:
    """Register an already evaluated artifact with validated lock and parity evidence."""
    from risklab.registry.register import register

    try:
        cfg_dir = _config_dir(ctx)
        version, run_id = register(lock, root=project_root(cfg_dir), config_dir=cfg_dir)
    except Exception as exc:
        _fail(str(exc))
    typer.echo(f"Registered version {version} (run {run_id}); alias unchanged")


@app.command("prepare-bad-candidate")
def prepare_bad_candidate_cmd(
    ctx: typer.Context,
    candidate: Annotated[Path, typer.Option("--candidate")],
) -> None:
    """Build the explicitly configured corrupted-label artifact for the rollback demo only."""
    from risklab.models.demo_candidate import prepare_bad_candidate

    try:
        cfg_dir = _config_dir(ctx)
        path = prepare_bad_candidate(candidate, config_dir=cfg_dir, root=project_root(cfg_dir))
    except Exception as exc:
        _fail(str(exc))
    typer.echo(f"Diagnostic candidate: {path}")
