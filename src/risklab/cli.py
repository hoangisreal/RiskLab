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
    load_raw_schema,
    project_root,
    resolve_config_dir,
)
from risklab.data.ingest import IngestError, run_ingest
from risklab.synthetic import generate

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
