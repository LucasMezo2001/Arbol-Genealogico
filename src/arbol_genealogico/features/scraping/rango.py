from __future__ import annotations

import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from arbol_genealogico.infrastructure.db.models import Archivo, FichaStatus, Sacramento, ScrapeFicha
from arbol_genealogico.infrastructure.scraper.client import ScraperClient
from arbol_genealogico.infrastructure.scraper.endpoints import ficha_param_name, ficha_path
from arbol_genealogico.infrastructure.scraper.parser import parse_ficha

logger = logging.getLogger(__name__)

# Tope de seguridad para la búsqueda binaria: por debajo del mayor ID real
# observado en AHDV-GEAH (~850.000 en bautismos) pero sin margen infinito
# para no hacer peticiones indefinidas si algo va mal.
LIMITE_BUSQUEDA = 5_000_000
LOTE_INSERT = 5000


async def _existe_ficha(client: ScraperClient, sacramento: Sacramento, id_registro: int) -> bool:
    param = ficha_param_name(sacramento)
    response = await client.get(ficha_path(sacramento), params={param: str(id_registro)})
    return parse_ficha(response.text, id_registro) is not None


async def descubrir_max_id(client: ScraperClient, sacramento: Sacramento, desde: int = 1) -> int:
    """Busca el mayor ID de registro existente para ``sacramento``.

    Archivos como AHDV-GEAH no exponen un contador público de registros, así
    que se descubre por sondeo: duplicando el ID hasta encontrar un hueco y
    luego acotando por búsqueda binaria. La numeración tiene huecos puntuales
    (IDs sueltos que no existen), así que el resultado es una cota superior
    segura, no el recuento exacto: quien encole por rango debe tratar cada ID
    individualmente y no asumir densidad total.
    """
    if not await _existe_ficha(client, sacramento, desde):
        return 0

    lo, hi = desde, desde
    while hi < LIMITE_BUSQUEDA and await _existe_ficha(client, sacramento, hi):
        lo = hi
        hi = hi * 2 if hi > 0 else 1

    while lo < hi:
        mid = (lo + hi + 1) // 2
        if await _existe_ficha(client, sacramento, mid):
            lo = mid
        else:
            hi = mid - 1
    return lo


async def encolar_rango(
    session: AsyncSession,
    archivo: Archivo,
    sacramento: Sacramento,
    id_min: int,
    id_max: int,
) -> int:
    """Encola en ``scrape_fichas`` todos los IDs de ``[id_min, id_max]``.

    A diferencia del flujo de AHEB-BEHA (listado -> IDs reales encontrados),
    aquí se encolan TODOS los IDs del rango, existan o no: los huecos se
    descartan al procesarlos (``FichaStatus.VACIO``, ver
    ``procesar_ficha.py``). Es idempotente (ON CONFLICT DO NOTHING).
    """
    total_insertadas = 0
    ids = range(id_min, id_max + 1)
    for inicio in range(0, len(ids), LOTE_INSERT):
        lote = ids[inicio : inicio + LOTE_INSERT]
        filas = [
            {
                "archivo": archivo,
                "id_registro": id_registro,
                "sacramento": sacramento,
                "id_localidad": None,
                "status": FichaStatus.PENDING,
            }
            for id_registro in lote
        ]
        stmt = pg_insert(ScrapeFicha).values(filas)
        stmt = stmt.on_conflict_do_nothing(index_elements=["archivo", "id_registro", "sacramento"])
        resultado = await session.execute(stmt)
        total_insertadas += resultado.rowcount or 0
        await session.commit()

    logger.info(
        "encolar_rango: %s/%s [%d, %d] -> %d filas nuevas",
        archivo.value,
        sacramento.value,
        id_min,
        id_max,
        total_insertadas,
    )
    return total_insertadas


async def descubrir_y_encolar(
    session: AsyncSession,
    client: ScraperClient,
    archivo: Archivo,
    sacramentos: tuple[Sacramento, ...] = (Sacramento.BAUTISMO, Sacramento.MATRIMONIO, Sacramento.DIFUNTO),
) -> dict[Sacramento, tuple[int, int]]:
    """Descubre el máximo ID de cada sacramento y encola el rango completo.

    Devuelve, por sacramento, ``(max_id_encontrado, filas_nuevas_encoladas)``.
    """
    resultado: dict[Sacramento, tuple[int, int]] = {}
    for sacramento in sacramentos:
        max_id = await descubrir_max_id(client, sacramento)
        if max_id == 0:
            logger.warning("descubrir_y_encolar: %s no tiene ningún registro en ID=1", sacramento.value)
            resultado[sacramento] = (0, 0)
            continue
        insertadas = await encolar_rango(session, archivo, sacramento, 1, max_id)
        resultado[sacramento] = (max_id, insertadas)
    return resultado
