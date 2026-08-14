"""Smoke tests del CLI."""

from __future__ import annotations

from typer.testing import CliRunner

from arbol_genealogico.entrypoints.cli import app

runner = CliRunner()


def test_hello_default() -> None:
    result = runner.invoke(app, ["hello"])
    assert result.exit_code == 0
    assert "Hola, mundo" in result.stdout


def test_hello_with_name() -> None:
    result = runner.invoke(app, ["hello", "--name", "Lucas"])
    assert result.exit_code == 0
    assert "Hola, Lucas" in result.stdout
