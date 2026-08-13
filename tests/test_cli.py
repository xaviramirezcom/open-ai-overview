"""Smoke tests for the ``own-overview`` CLI via Typer's CliRunner.

Runs the real commands in-process against the local stack (seed writes to a
per-test temp CDA root and store, per conftest), so this covers the CLI wiring
end to end without cloud or a subprocess.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("pyarrow")

from typer.testing import CliRunner

from own_overview.cli import app

runner = CliRunner()


def test_seed_then_query_by_role():
    seed = runner.invoke(app, ["seed"])
    assert seed.exit_code == 0, seed.output
    assert "indexed" in seed.output

    adjuster = runner.invoke(
        app, ["query", "Why did the premium on POL-55012 go up?", "--role", "adjuster"]
    )
    assert adjuster.exit_code == 0, adjuster.output

    underwriter = runner.invoke(
        app, ["query", "Why did the premium on POL-55012 go up?", "--role", "underwriter"]
    )
    assert underwriter.exit_code == 0, underwriter.output
    # The underwriter's answer draws on the underwriting memo; the adjuster's
    # does not. (The role label prints too, so check the source citation.)
    assert "underwriting/" in underwriter.output
    assert "underwriting/" not in adjuster.output


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("seed", "ingest", "query"):
        assert cmd in result.output
