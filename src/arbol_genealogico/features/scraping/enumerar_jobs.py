from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from arbol_genealogico.infrastructure.db.models import JobStatus, Localidad, Sacramento, ScrapeJob

logger = logging.getLogger(__name__)

ANIO_MIN = 1501
ANIO_MAX = 1900


async def enumerar_jobs(
    session: AsyncSession,
    anio_min: int = ANIO_MIN,
    anio_max: int = ANIO_MAX,
    sacramentos: tuple[Sacramento, ...] = (Sacramento.BAUTISMO, Sacramento.MATRIMONIO, Sacramento.DIFUNTO),
) -> int:
    """Rellena ``scrape_jobs`` con un job por (sacramento, localidad, año).

    Granularidad de 1 año: incluso Bilbao (la localidad más grande) tiene del
    orden de mil registros/año por sacramento, muy por debajo del límite
    práctico de páginas por job. Idempotente (ON CONFLICT DO NOTHING).
    """
    stmt_localidades = select(Localidad.id_localidad).order_by(Localidad.id_localidad)
    localidad_ids = (await session.execute(stmt_localidades)).scalars().all()
    if not localidad_ids:
        raise RuntimeError("No hay localidades cargadas: ejecuta primero el seed (arbol db seed)")

    filas = [
        {
            "sacramento": sacramento,
            "id_localidad": id_localidad,
            "anio_ini": anio,
            "anio_fin": anio,
            "status": JobStatus.PENDING,
        }
        for sacramento in sacramentos
        for id_localidad in localidad_ids
        for anio in range(anio_min, anio_max + 1)
    ]

    total_insertadas = 0
    lote = 5000
    for inicio in range(0, len(filas), lote):
        stmt = pg_insert(ScrapeJob).values(filas[inicio : inicio + lote])
        stmt = stmt.on_conflict_do_nothing(index_elements=["sacramento", "id_localidad", "anio_ini", "anio_fin"])
        resultado = await session.execute(stmt)
        total_insertadas += resultado.rowcount or 0
        await session.commit()

    logger.info("enumerar_jobs: %d combinaciones evaluadas, %d jobs nuevos insertados", len(filas), total_insertadas)
    return total_insertadas
