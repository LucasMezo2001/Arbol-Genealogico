from __future__ import annotations

import logging
import math
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from arbol_genealogico.infrastructure.db.models import FichaStatus, JobStatus, ScrapeFicha, ScrapeJob
from arbol_genealogico.infrastructure.scraper.client import ScraperClient
from arbol_genealogico.infrastructure.scraper.endpoints import fetch_listado_html
from arbol_genealogico.infrastructure.scraper.parser import parse_listado

logger = logging.getLogger(__name__)

RESULTADOS_POR_PAGINA = 100
MAX_ERROR_LEN = 2000


async def _encolar_fichas(session: AsyncSession, sacramento: str, id_localidad: int, ids: list[int]) -> None:
    if not ids:
        return
    filas = [
        {
            "id_registro": id_registro,
            "sacramento": sacramento,
            "id_localidad": id_localidad,
            "status": FichaStatus.PENDING,
        }
        for id_registro in ids
    ]
    stmt = pg_insert(ScrapeFicha).values(filas)
    stmt = stmt.on_conflict_do_nothing(index_elements=["id_registro", "sacramento"])
    await session.execute(stmt)


async def procesar_job(session: AsyncSession, client: ScraperClient, job: ScrapeJob) -> None:
    """Descarga y pagina el listado de un job, encolando cada id en scrape_fichas."""
    job.status = JobStatus.RUNNING
    job.iniciado_en = datetime.now(UTC)
    await session.commit()

    try:
        pagina = job.ultima_pagina + 1 if job.ultima_pagina else 1
        total_paginas: int | None = job.paginas_totales
        while True:
            html = await fetch_listado_html(
                client, job.sacramento, job.id_localidad, job.anio_ini, job.anio_fin, pagina
            )
            resultado = parse_listado(html, job.sacramento)

            if resultado.total_registros is not None:
                job.total_registros = resultado.total_registros
                total_paginas = max(1, math.ceil(resultado.total_registros / RESULTADOS_POR_PAGINA))
                job.paginas_totales = total_paginas

            await _encolar_fichas(session, job.sacramento.value, job.id_localidad, resultado.ids)
            job.ultima_pagina = pagina
            await session.commit()

            if resultado.sin_resultados or not resultado.ids:
                break
            if total_paginas is None or pagina >= total_paginas:
                break
            pagina += 1

        job.status = JobStatus.DONE
        job.finalizado_en = datetime.now(UTC)
        job.error = None
    except Exception as exc:  # noqa: BLE001 - se persiste el error para poder reanudar
        logger.exception("Error procesando scrape_job %s", job.id)
        job.status = JobStatus.ERROR
        job.retries += 1
        job.error = str(exc)[:MAX_ERROR_LEN]

    await session.commit()


async def procesar_listados(session: AsyncSession, client: ScraperClient, limite: int | None = None) -> int:
    """Procesa jobs `pending` (o `error` con pocos reintentos) uno a uno."""
    stmt = (
        select(ScrapeJob)
        .where(ScrapeJob.status.in_([JobStatus.PENDING, JobStatus.ERROR]))
        .where(ScrapeJob.retries < 5)
        .order_by(ScrapeJob.id)
    )
    if limite is not None:
        stmt = stmt.limit(limite)

    jobs = (await session.execute(stmt)).scalars().all()
    procesados = 0
    for job in jobs:
        await procesar_job(session, client, job)
        procesados += 1
    return procesados
