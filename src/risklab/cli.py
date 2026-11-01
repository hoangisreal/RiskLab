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
from risklab.experiments.diagnostics import run_diagnostics
from risklab.experiments.e1 import E1Error, run_e1
from risklab.models.evaluate import EvaluationError
from risklab.synthetic import generate
from risklab.tracking import TrackingError

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
        typer.Option(
            "--no-tracking",
            help="Skip MLflow. Results are then not reportable (no run ids).",
        ),
    ] = False,
) -> None:
    """Run E1 (Logistic Regression vs LightGBM, application features) on Train and Selection."""
    try:
        cfg_dir = _config_dir(ctx)
        data_cfg = load_data_config(cfg_dir)
        summary = run_e1(
            config_dir=cfg_dir,
            root=project_root(cfg_dir),
            output_dir=data_cfg.reports_dir / "experiments" / "e1",
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
def diagnostics(
    ctx: typer.Context,
    no_tracking: Annotated[
        bool, typer.Option("--no-tracking", help="Skip MLflow (results are then not reportable).")
    ] = False,
) -> None:
    """Run the leakage diagnostics (label permutation, single-feature screening) on Train."""
    try:
        cfg_dir = _config_dir(ctx)
        data_cfg = load_data_config(cfg_dir)
        report = run_diagnostics(
            config_dir=cfg_dir,
            root=project_root(cfg_dir),
            output_dir=data_cfg.reports_dir / "diagnostics",
            data_cfg=data_cfg,
            tracking=not no_tracking,
            log=typer.echo,
        )
    except (ConfigError, LoaderError, PartitionError, TrackingError) as exc:
        _fail(str(exc))
    typer.echo(f"Diagnostics {'passed' if report['passed'] else 'NEED REVIEW'}")
    if report["run_id"]:
        typer.echo(f"MLflow run: {report['run_id']}")
    if not report["passed"]:
        raise typer.Exit(code=1)
