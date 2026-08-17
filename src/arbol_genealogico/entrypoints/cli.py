from __future__ import annotations

import asyncio
import subprocess
import time
from contextlib import AsyncExitStack

import typer
from sqlalchemy import func, select, text

from arbol_genealogico.config.paths import project_root
from arbol_genealogico.config.settings import AppSettings
from arbol_genealogico.infrastructure.db.models import (
    Archivo,
    FichaStatus,
    JobStatus,
    Sacramento,
    ScrapeFicha,
    ScrapeJob,
)
from arbol_genealogico.infrastructure.db.seed import seed_localidades
from arbol_genealogico.infrastructure.db.session import get_session
from arbol_genealogico.infrastructure.logger import configure_logging
from arbol_genealogico.infrastructure.scraper.client import ScraperClient, build_client

app = typer.Typer(help="CLI del Árbol Genealógico")
db_app = typer.Typer(help="Gestión de la base de datos (Docker + Alembic + seeds)")
scrape_app = typer.Typer(help="Orquestación del scraper (AHEB-BEHA, AHDV-GEAH y AHDSS)")

_ARCHIVOS_POR_NOMBRE = {a.value: a for a in Archivo}


def _parse_archivo(valor: str) -> Archivo:
    try:
        return _ARCHIVOS_POR_NOMBRE[valor]
    except KeyError as exc:
        opciones = ", ".join(_ARCHIVOS_POR_NOMBRE)
        raise typer.BadParameter(f"Archivo desconocido '{valor}'. Opciones: {opciones}") from exc


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
    """[Sólo AHEB-BEHA] Rellena scrape_jobs con las combinaciones sacramento × localidad × año pendientes."""
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
    """[Sólo AHEB-BEHA] Descarga y pagina los listados pendientes, encolando cada id en scrape_fichas."""
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


@scrape_app.command("rango")
def scrape_rango(
    archivo: str = typer.Option("ahdv_geah", help="Archivo a recorrer por rango de ID (ahdv_geah|ahdss|aheb_beha)"),
    sacramento: str | None = typer.Option(None, help="bautismo|matrimonio|difunto (por defecto, los 3)"),
) -> None:
    """Descubre el máximo ID existente y encola scrape_fichas con todo el rango [1, max].

    Pensado para archivos sin motor de búsqueda por localidad válido para
    barrido exhaustivo (AHDV-GEAH, AHDSS): cada ficha se descarga
    directamente por ID, sin pasar por listados. Los huecos de numeración
    se descartan al procesar (``FichaStatus.VACIO``), así que es normal que
    no todos los IDs del rango correspondan a un registro real. En AHDSS el
    ID es global: se encolan 3 filas por ID (auditoría: 1 DONE + 2 VACIO),
    pero el worker no hace 3 GET (early-stop; ver ``procesar_ficha.py``).
    """
    from arbol_genealogico.features.scraping.rango import descubrir_y_encolar, descubrir_y_encolar_ahdss

    archivo_enum = _parse_archivo(archivo)
    sacramentos = (Sacramento(sacramento),) if sacramento else tuple(Sacramento)

    async def _rango() -> dict[Sacramento, tuple[int, int]]:
        settings = AppSettings()
        async with build_client(settings, archivo_enum) as client, get_session() as session:
            if archivo_enum == Archivo.AHDSS:
                return await descubrir_y_encolar_ahdss(session, client, sacramentos)
            return await descubrir_y_encolar(session, client, archivo_enum, sacramentos)

    resultado = asyncio.run(_rango())
    for sac, (max_id, insertadas) in resultado.items():
        typer.echo(f"{sac.value}: max_id={max_id}, fichas nuevas encoladas={insertadas}")


@scrape_app.command("fichas")
def scrape_fichas(
    lote: int = typer.Option(
        200,
        help="Unidades de trabajo por lote (1 fila; en AHDSS, 1 id_registro distinto)",
    ),
    continuo: bool = typer.Option(True, help="Repetir hasta agotar las fichas pendientes"),
    archivo: str | None = typer.Option(
        None, help="Limitar a un archivo (aheb_beha|ahdv_geah|ahdss); por defecto, todos"
    ),
) -> None:
    """Descarga y parsea las fichas pendientes, upsertando personas/parroquias/fondos/registros."""
    from arbol_genealogico.features.scraping.procesar_ficha import procesar_fichas

    archivo_enum = _parse_archivo(archivo) if archivo else None
    settings = AppSettings()
    archivos_a_abrir = [archivo_enum] if archivo_enum else list(Archivo)

    async def _run_all() -> int:
        total = 0
        clientes = {a: build_client(settings, a) for a in archivos_a_abrir}
        async with AsyncExitStack() as stack:
            clients = {a: await stack.enter_async_context(c) for a, c in clientes.items()}
            async with get_session() as session:
                while True:
                    procesados = await procesar_fichas(session, clients, limite=lote, archivo=archivo_enum)
                    total += procesados
                    typer.echo(f"Fichas procesadas en este lote: {procesados} (acumulado: {total})")
                    if not continuo or procesados == 0:
                        break
        return total

    total = asyncio.run(_run_all())
    typer.echo(f"Fichas procesadas: {total}")


@scrape_app.command("all")
def scrape_all(
    anio_min: int = typer.Option(1501, help="[AHEB-BEHA] Año inicial del plan (inclusive)"),
    anio_max: int = typer.Option(1900, help="[AHEB-BEHA] Año final del plan (inclusive)"),
    lote_listados: int = typer.Option(50, help="Jobs de listado por lote"),
    lote_fichas: int = typer.Option(200, help="Unidades de trabajo de fichas por lote (en AHDSS, IDs distintos)"),
    con_rango: bool = typer.Option(
        False,
        help=(
            "También descubrir y encolar el rango completo de AHDV-GEAH y AHDSS "
            "(millones de IDs; por defecto no, para no encolar Álava/Gipuzkoa por accidente)"
        ),
    ),
    sin_plan: bool = typer.Option(False, help="Saltar la fase de plan (AHEB-BEHA)"),
    sin_listados: bool = typer.Option(False, help="Saltar la fase de listados (AHEB-BEHA)"),
    sin_fichas: bool = typer.Option(False, help="Saltar la fase de fichas"),
    continuo: bool = typer.Option(
        True,
        help="Tras encolar, repetir listados+fichas hasta agotar colas (con --no-continuo: un lote de cada)",
    ),
) -> None:
    """Orquesta plan + listados + fichas (y opcionalmente rango) en un solo comando.

    Flujo por defecto:

    1. ``plan`` (AHEB-BEHA): rellena ``scrape_jobs`` si faltan combinaciones.
    2. Si ``--con-rango``: descubre y encola ``[1, max]`` de AHDV-GEAH y AHDSS.
    3. Bucle que alterna un lote de ``listados`` (Bizkaia) y un lote de
       ``fichas`` (cola compartida de los tres archivos) hasta agotar ambas
       colas, o un solo ciclo de cada uno con ``--no-continuo``.

    Sin ``--con-rango`` no encola Álava/Gipuzkoa nuevos, pero sí procesa las
    fichas de esos archivos que ya estuvieran pendientes en la cola.
    """
    from arbol_genealogico.features.scraping.enumerar_jobs import enumerar_jobs
    from arbol_genealogico.features.scraping.procesar_ficha import procesar_fichas
    from arbol_genealogico.features.scraping.procesar_listado import procesar_listados
    from arbol_genealogico.features.scraping.rango import descubrir_y_encolar, descubrir_y_encolar_ahdss

    settings = AppSettings()

    async def _run() -> None:
        async with AsyncExitStack() as stack:
            clientes = {a: await stack.enter_async_context(build_client(settings, a)) for a in Archivo}
            async with get_session() as session:
                if not sin_plan:
                    typer.echo("=== Fase plan (AHEB-BEHA) ===")
                    jobs_nuevos = await enumerar_jobs(session, anio_min=anio_min, anio_max=anio_max)
                    typer.echo(f"Jobs nuevos insertados: {jobs_nuevos}")

                if con_rango:
                    typer.echo("=== Fase rango (AHDV-GEAH) ===")
                    for sac, (max_id, insertadas) in (
                        await descubrir_y_encolar(session, clientes[Archivo.AHDV_GEAH], Archivo.AHDV_GEAH)
                    ).items():
                        typer.echo(f"ahdv_geah/{sac.value}: max_id={max_id}, nuevas={insertadas}")

                    typer.echo("=== Fase rango (AHDSS) ===")
                    for sac, (max_id, insertadas) in (
                        await descubrir_y_encolar_ahdss(session, clientes[Archivo.AHDSS])
                    ).items():
                        typer.echo(f"ahdss/{sac.value}: max_id={max_id}, nuevas={insertadas}")
                else:
                    typer.echo(
                        "Omitiendo rango de AHDV-GEAH/AHDSS (pasa --con-rango para encolar su barrido completo)."
                    )

                if sin_listados and sin_fichas:
                    typer.echo("Nada más que hacer (--sin-listados y --sin-fichas).")
                    return

                typer.echo("=== Fase trabajo (listados + fichas) ===")
                total_listados = 0
                total_fichas = 0
                while True:
                    listados_lote = 0
                    fichas_lote = 0
                    if not sin_listados:
                        listados_lote = await procesar_listados(
                            session, clientes[Archivo.AHEB_BEHA], limite=lote_listados
                        )
                        total_listados += listados_lote
                        typer.echo(f"Listados en este ciclo: {listados_lote} (acumulado: {total_listados})")
                    if not sin_fichas:
                        fichas_lote = await procesar_fichas(session, clientes, limite=lote_fichas)
                        total_fichas += fichas_lote
                        typer.echo(f"Fichas en este ciclo: {fichas_lote} (acumulado: {total_fichas})")

                    if not continuo:
                        break
                    if listados_lote == 0 and fichas_lote == 0:
                        break

                typer.echo(f"Listados procesados: {total_listados}")
                typer.echo(f"Fichas procesadas: {total_fichas}")

    asyncio.run(_run())


@scrape_app.command("status")
def scrape_status() -> None:
    """Muestra el progreso: jobs y fichas por estado, desglosado por archivo."""

    async def _status() -> tuple[dict[str, int], dict[tuple[str, str], int]]:
        async with get_session() as session:
            jobs_result = await session.execute(select(ScrapeJob.status, func.count()).group_by(ScrapeJob.status))
            fichas_result = await session.execute(
                select(ScrapeFicha.archivo, ScrapeFicha.status, func.count()).group_by(
                    ScrapeFicha.archivo, ScrapeFicha.status
                )
            )
            jobs = {status.value: count for status, count in jobs_result.all()}
            fichas = {(archivo.value, status.value): count for archivo, status, count in fichas_result.all()}
            return jobs, fichas

    jobs, fichas = asyncio.run(_status())

    typer.echo("Jobs (listados, sólo AHEB-BEHA):")
    for status in JobStatus:
        typer.echo(f"  {status.value:>8}: {jobs.get(status.value, 0)}")
    typer.echo(f"  {'total':>8}: {sum(jobs.values())}")

    archivos_con_datos = sorted({archivo for archivo, _ in fichas})
    for archivo in archivos_con_datos or [a.value for a in Archivo]:
        typer.echo(f"Fichas (detalle) - {archivo}:")
        por_archivo = {status: count for (a, status), count in fichas.items() if a == archivo}
        for status in FichaStatus:
            typer.echo(f"  {status.value:>8}: {por_archivo.get(status.value, 0)}")
        typer.echo(f"  {'total':>8}: {sum(por_archivo.values())}")
        pendientes = por_archivo.get(FichaStatus.PENDING.value, 0) + por_archivo.get(FichaStatus.ERROR.value, 0)
        if pendientes > 0:
            typer.echo(f"  Pendientes de procesar: {pendientes}")


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
