from __future__ import annotations

import typer

app = typer.Typer(help="CLI del Árbol Genealógico")


@app.callback()
def main() -> None:
    """Punto de entrada CLI."""


@app.command("hello")
def hello(name: str = "mundo") -> None:
    """Comando de ejemplo."""
    typer.echo(f"Hola, {name}")


if __name__ == "__main__":
    app()
