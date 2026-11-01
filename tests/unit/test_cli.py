import pytest
from typer.testing import CliRunner

from risklab import __version__
from risklab.cli import app

pytestmark = pytest.mark.unit

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("version", "ingest", "synthetic", "--config-dir"):
        assert cmd in result.output


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__
