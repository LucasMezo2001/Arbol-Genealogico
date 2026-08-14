from __future__ import annotations

import asyncio
import subprocess
import time

import typer
from sqlalchemy import func, select, text

from arbol_genealogico.config.paths import project_root
from arbol_genealogico.infrastructure.db.models import FichaStatus, JobStatus, ScrapeFicha, ScrapeJob
from arbol_genealogico.infrastructure.db.seed import seed_localidades
from arbol_genealogico.infrastructure.db.session import get_session
from arbol_genealogico.infrastructure.logger import configure_logging
from arbol_genealogico.infrastructure.scraper.client import ScraperClient

app = typer.Typer(help="CLI del Árbol Genealógico")
db_app = typer.Typer(help="Gestión de la base de datos (Docker + Alembic + seeds)")
scrape_app = typer.Typer(help="Orquestación del scraper AHEB-BEHA")
app.add_typer(db_app, name="db")
app.add_typer(scrape_app, name="scrape")


@app.callback()
def main() -> None:
    """Punto de entrada CLI."""
    configure_logging()


@app.command("hello")
def hello(name: str = "mundo") -> None:
    """Comando de ejemplo."""
    typer.echo(f"Hola, {name}")


def _run(cmd: list[str]) -> None:
    typer.echo(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=project_root(), check=True)


@db_app.command("up")
def db_up() -> None:
    """Levanta Postgres/pgAdmin con Docker Compose y aplica las migraciones de Alembic."""
    _run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env",
            "-f",
            "docker/docker-compose.yml",
            "up",
            "-d",
        ]
    )
    typer.echo("Esperando a que Postgres esté listo...")
    time.sleep(3)
    _run(["poetry", "run", "alembic", "upgrade", "head"])
    typer.echo("Base de datos lista.")


@db_app.command("down")
def db_down() -> None:
    """Detiene los contenedores de Docker Compose (conserva los volúmenes)."""
    _run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env",
            "-f",
            "docker/docker-compose.yml",
            "down",
        ]
    )


@db_app.command("seed")
def db_seed() -> None:
    """Precarga las 115 localidades de referencia."""

    async def _seed() -> int:
        async with get_session() as session:
            return await seed_localidades(session)

    total = asyncio.run(_seed())
    typer.echo(f"Localidades cargadas/actualizadas: {total}")


@scrape_app.command("plan")
def scrape_plan(
    anio_min: int = typer.Option(1501, help="Año inicial (inclusive)"),
    anio_max: int = typer.Option(1900, help="Año final (inclusive)"),
) -> None:
    """Rellena scrape_jobs con todas las combinaciones sacramento × localidad × año pendientes."""
    from arbol_genealogico.features.scraping.enumerar_jobs import enumerar_jobs

    async def _plan() -> int:
        async with get_session() as session:
            return await enumerar_jobs(session, anio_min=anio_min, anio_max=anio_max)

    total = asyncio.run(_plan())
    typer.echo(f"Jobs nuevos insertados: {total}")


@scrape_app.command("listados")
def scrape_listados(
    lote: int = typer.Option(50, help="Jobs a procesar por lote"),
    continuo: bool = typer.Option(True, help="Repetir hasta agotar los jobs pendientes"),
) -> None:
    """Descarga y pagina los listados pendientes, encolando cada id en scrape_fichas."""
    from arbol_genealogico.features.scraping.procesar_listado import procesar_listados

    async def _run_all() -> int:
        total = 0
        async with ScraperClient() as client, get_session() as session:
            while True:
                procesados = await procesar_listados(session, client, limite=lote)
                total += procesados
                typer.echo(f"Jobs procesados en este lote: {procesados} (acumulado: {total})")
                if not continuo or procesados == 0:
                    break
        return total

    total = asyncio.run(_run_all())
    typer.echo(f"Listados procesados: {total}")


@scrape_app.command("fichas")
def scrape_fichas(
    lote: int = typer.Option(200, help="Fichas a procesar por lote"),
    continuo: bool = typer.Option(True, help="Repetir hasta agotar las fichas pendientes"),
) -> None:
    """Descarga y parsea las fichas pendientes, upsertando personas/parroquias/fondos/registros."""
    from arbol_genealogico.features.scraping.procesar_ficha import procesar_fichas

    async def _run_all() -> int:
        total = 0
        async with ScraperClient() as client, get_session() as session:
            while True:
                procesados = await procesar_fichas(session, client, limite=lote)
                total += procesados
                typer.echo(f"Fichas procesadas en este lote: {procesados} (acumulado: {total})")
                if not continuo or procesados == 0:
                    break
        return total

    total = asyncio.run(_run_all())
    typer.echo(f"Fichas procesadas: {total}")


@scrape_app.command("status")
def scrape_status() -> None:
    """Muestra el progreso: jobs y fichas por estado."""

    async def _status() -> tuple[dict[str, int], dict[str, int]]:
        async with get_session() as session:
            jobs_result = await session.execute(select(ScrapeJob.status, func.count()).group_by(ScrapeJob.status))
            fichas_result = await session.execute(select(ScrapeFicha.status, func.count()).group_by(ScrapeFicha.status))
            jobs = {status.value: count for status, count in jobs_result.all()}
            fichas = {status.value: count for status, count in fichas_result.all()}
            return jobs, fichas

    jobs, fichas = asyncio.run(_status())

    typer.echo("Jobs (listados):")
    for status in JobStatus:
        typer.echo(f"  {status.value:>8}: {jobs.get(status.value, 0)}")
    total_jobs = sum(jobs.values())
    typer.echo(f"  {'total':>8}: {total_jobs}")

    typer.echo("Fichas (detalle):")
    for status in FichaStatus:
        typer.echo(f"  {status.value:>8}: {fichas.get(status.value, 0)}")
    total_fichas = sum(fichas.values())
    typer.echo(f"  {'total':>8}: {total_fichas}")

    pendientes = fichas.get(FichaStatus.PENDING.value, 0) + fichas.get(FichaStatus.ERROR.value, 0)
    if pendientes > 0:
        typer.echo(f"Fichas pendientes de procesar: {pendientes}")


@app.command("query")
def query(sql: str) -> None:
    """Ejecuta una consulta SQL de sólo lectura y muestra el resultado (atajo de conveniencia)."""

    async def _query() -> tuple[list[str], list[tuple]]:
        async with get_session() as session:
            result = await session.execute(text(sql))
            columns = list(result.keys())
            rows = result.fetchall()
            return columns, [tuple(row) for row in rows]

    columns, rows = asyncio.run(_query())
    typer.echo(" | ".join(columns))
    for row in rows:
        typer.echo(" | ".join(str(value) for value in row))
    typer.echo(f"({len(rows)} filas)")


if __name__ == "__main__":
    app()
