import re

import pytest
from typer.testing import CliRunner

from risklab import __version__
from risklab.cli import app

pytestmark = pytest.mark.unit

runner = CliRunner()


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def test_help() -> None:
    # Rich may colour and wrap help output (CI sets FORCE_COLOR), so pin and strip it.
    result = runner.invoke(
        app, ["--help"], env={"NO_COLOR": "1", "FORCE_COLOR": "", "COLUMNS": "200", "TERM": "dumb"}
    )
    assert result.exit_code == 0
    output = ANSI.sub("", result.output)
    for cmd in ("version", "ingest", "partition", "e1", "diagnostics", "synthetic", "--config-dir"):
        assert cmd in output


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__
