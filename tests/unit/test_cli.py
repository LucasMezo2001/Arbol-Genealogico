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


def test_scrape_all_help() -> None:
    result = runner.invoke(app, ["scrape", "all", "--help"])
    assert result.exit_code == 0
    assert "--con-rango" in result.stdout
    assert "--sin-plan" in result.stdout
    assert "--sin-listados" in result.stdout
    assert "--sin-fichas" in result.stdout
    assert "Orquesta plan" in result.stdout or "plan" in result.stdout.lower()
