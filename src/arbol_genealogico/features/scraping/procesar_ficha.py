from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from arbol_genealogico.infrastructure.db.models import (
    Archivo,
    Bautismo,
    Defuncion,
    FichaStatus,
    Fondo,
    Localidad,
    Matrimonio,
    Parroquia,
    Persona,
    Sacramento,
    ScrapeFicha,
)
from arbol_genealogico.infrastructure.scraper.client import ScraperClient
from arbol_genealogico.infrastructure.scraper.endpoints import fetch_ficha_html, fetch_ficha_html_ahdss
from arbol_genealogico.infrastructure.scraper.parser import (
    FichaCompleta,
    PersonaDatos,
    parse_ficha,
    parse_ficha_ahdss,
)

logger = logging.getLogger(__name__)

MAX_ERROR_LEN = 2000


async def _upsert_persona(session: AsyncSession, persona: PersonaDatos | None) -> int | None:
    if persona is None:
        return None
    stmt = (
        pg_insert(Persona)
        .values(nombre=persona.nombre, apellido1=persona.apellido1, apellido2=persona.apellido2)
        .on_conflict_do_update(
            index_elements=[Persona.nombre, Persona.apellido1, Persona.apellido2],
            set_={"nombre": persona.nombre},
        )
        .returning(Persona.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def _upsert_localidad_por_nombre(session: AsyncSession, archivo: Archivo, nombre: str | None) -> int | None:
    """Resuelve/crea una localidad por nombre para archivos sin ID conocido de antemano.

    AHEB-BEHA siempre trae el ``id_localidad`` desde el job/listado que
    generó la ficha (ver ``item.id_localidad``); AHDV-GEAH se recorre por
    rango de ID sin búsqueda previa, así que la localidad sólo se conoce al
    leer la ficha y hay que resolverla por nombre.
    """
    if not nombre:
        return None
    stmt = (
        pg_insert(Localidad)
        .values(archivo=archivo, nombre=nombre)
        .on_conflict_do_update(index_elements=[Localidad.archivo, Localidad.nombre], set_={"nombre": nombre})
        .returning(Localidad.id_localidad)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def _upsert_parroquia(
    session: AsyncSession, archivo: Archivo, ficha: FichaCompleta, id_localidad: int | None
) -> int | None:
    if ficha.parroquia_nombre is None and ficha.parroquia_codigo is None:
        return None
    stmt = (
        pg_insert(Parroquia)
        .values(
            archivo=archivo,
            codigo=ficha.parroquia_codigo,
            nombre=ficha.parroquia_nombre,
            id_localidad=id_localidad,
            diocesis=ficha.diocesis,
            territorio_historico=ficha.territorio_historico,
            localidad_texto=ficha.localidad_texto,
        )
        .on_conflict_do_update(
            index_elements=[Parroquia.codigo, Parroquia.nombre],
            set_={
                "diocesis": ficha.diocesis,
                "territorio_historico": ficha.territorio_historico,
                "localidad_texto": ficha.localidad_texto,
            },
        )
        .returning(Parroquia.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def _upsert_fondo(session: AsyncSession, archivo: Archivo, ficha: FichaCompleta) -> int | None:
    if ficha.fondo_codigo is None:
        return None
    stmt = (
        pg_insert(Fondo)
        .values(archivo=archivo, codigo=ficha.fondo_codigo, descripcion=ficha.fondo_descripcion)
        .on_conflict_do_update(
            index_elements=[Fondo.codigo],
            set_={"descripcion": ficha.fondo_descripcion},
        )
        .returning(Fondo.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


# Tope de filas por round-trip a scrape_fichas: la cola de AHDSS puede
# tener ~5.9M PENDING; nunca se materializa entera en memoria.
LOTE_MAX_FILAS = 2000
MAX_RETRIES = 5
_SACRAMENTOS_AHDSS = (Sacramento.BAUTISMO, Sacramento.MATRIMONIO, Sacramento.DIFUNTO)


def _parse_fecha(raw: str | None) -> dt.date | None:
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _es_procesable(item: ScrapeFicha) -> bool:
    return item.status in (FichaStatus.PENDING, FichaStatus.ERROR) and item.retries < MAX_RETRIES


def _marcar_vacio(item: ScrapeFicha) -> None:
    item.status = FichaStatus.VACIO
    item.scraped_at = dt.datetime.now(dt.UTC)
    item.error = None


def _marcar_error(item: ScrapeFicha, exc: BaseException) -> None:
    logger.exception("Error procesando scrape_ficha %s/%s", item.sacramento, item.id_registro)
    item.status = FichaStatus.ERROR
    item.retries += 1
    item.error = str(exc)[:MAX_ERROR_LEN]


def _orden_intentos_ahdss(primero: Sacramento) -> list[Sacramento]:
    """Sacramento que saltó el worker, luego B/M/D sin repetir."""
    return [primero, *[s for s in _SACRAMENTOS_AHDSS if s != primero]]


def _filas_a_cargar(remaining: int | None, archivo: Archivo | None) -> int:
    """Cuántas filas pedir a la BD en este round-trip.

    En AHDSS (o cola mixta) un ID de trabajo son hasta 3 filas; AHEB-BEHA y
    AHDV-GEAH cuentan 1 fila = 1 trabajo.
    """
    if remaining is None:
        return LOTE_MAX_FILAS
    if archivo in (None, Archivo.AHDSS):
        return min(LOTE_MAX_FILAS, max(remaining * 3, 1))
    return min(LOTE_MAX_FILAS, remaining)


async def _cargar_hermanas(session: AsyncSession, archivo: Archivo, id_registro: int) -> list[ScrapeFicha]:
    stmt = select(ScrapeFicha).where(
        ScrapeFicha.archivo == archivo,
        ScrapeFicha.id_registro == id_registro,
    )
    return list((await session.execute(stmt)).scalars().all())


async def _cargar_pendientes(session: AsyncSession, archivo: Archivo | None, limite_filas: int) -> list[ScrapeFicha]:
    stmt = (
        select(ScrapeFicha)
        .where(ScrapeFicha.status.in_([FichaStatus.PENDING, FichaStatus.ERROR]))
        .where(ScrapeFicha.retries < MAX_RETRIES)
        .order_by(ScrapeFicha.id)
        .limit(limite_filas)
    )
    if archivo is not None:
        stmt = stmt.where(ScrapeFicha.archivo == archivo)
    return list((await session.execute(stmt)).scalars().all())


async def _persistir_ficha_ok(
    session: AsyncSession, client: ScraperClient, item: ScrapeFicha, html: str, ficha: FichaCompleta
) -> None:
    """Upsert de personas/parroquia/fondo/registro y marca la fila DONE."""
    id_localidad = item.id_localidad
    if id_localidad is None:
        nombre_localidad = ficha.municipio_texto or ficha.localidad_texto
        id_localidad = await _upsert_localidad_por_nombre(session, item.archivo, nombre_localidad)

    id_fondo = await _upsert_fondo(session, item.archivo, ficha)
    id_parroquia = await _upsert_parroquia(session, item.archivo, ficha, id_localidad)
    fecha = _parse_fecha(ficha.fecha)
    campos_comunes = {
        "fecha": fecha,
        "comentarios": ficha.comentarios,
        "codigo_referencia": ficha.codigo_referencia,
        "signatura": ficha.signatura,
        "sig_antigua": ficha.sig_antigua,
        "sig_microfilm": ficha.sig_microfilm,
        "sig_digital": ficha.sig_digital,
        "sig_digital_libro": ficha.sig_digital_libro,
        "pagina": ficha.pagina,
        "fechas_libro": ficha.fechas_libro,
        "id_fondo": id_fondo,
        "id_parroquia": id_parroquia,
        "id_localidad": id_localidad,
        "html_sha256": client.sha256(html),
    }

    if item.sacramento == Sacramento.BAUTISMO:
        id_persona = await _upsert_persona(session, ficha.persona)
        id_padre = await _upsert_persona(session, ficha.padre)
        id_madre = await _upsert_persona(session, ficha.madre)
        stmt = (
            pg_insert(Bautismo)
            .values(
                archivo=item.archivo,
                id_bautismo=item.id_registro,
                id_persona=id_persona,
                id_padre=id_padre,
                id_madre=id_madre,
                **campos_comunes,
            )
            .on_conflict_do_update(index_elements=[Bautismo.archivo, Bautismo.id_bautismo], set_=campos_comunes)
        )
    elif item.sacramento == Sacramento.MATRIMONIO:
        id_esposo = await _upsert_persona(session, ficha.esposo)
        id_esposa = await _upsert_persona(session, ficha.esposa)
        stmt = (
            pg_insert(Matrimonio)
            .values(
                archivo=item.archivo,
                id_matrimonio=item.id_registro,
                id_esposo=id_esposo,
                id_esposa=id_esposa,
                **campos_comunes,
            )
            .on_conflict_do_update(index_elements=[Matrimonio.archivo, Matrimonio.id_matrimonio], set_=campos_comunes)
        )
    else:
        id_persona = await _upsert_persona(session, ficha.persona)
        id_conyuge = await _upsert_persona(session, ficha.conyuge)
        id_padre = await _upsert_persona(session, ficha.padre)
        id_madre = await _upsert_persona(session, ficha.madre)
        stmt = (
            pg_insert(Defuncion)
            .values(
                archivo=item.archivo,
                id_difunto=item.id_registro,
                id_persona=id_persona,
                id_conyuge=id_conyuge,
                id_padre=id_padre,
                id_madre=id_madre,
                edad=ficha.edad,
                **campos_comunes,
            )
            .on_conflict_do_update(
                index_elements=[Defuncion.archivo, Defuncion.id_difunto],
                set_={**campos_comunes, "edad": ficha.edad},
            )
        )

    await session.execute(stmt)
    item.status = FichaStatus.DONE
    item.scraped_at = dt.datetime.now(dt.UTC)
    item.error = None


def _cerrar_hermanas_pendientes(hermanas: list[ScrapeFicha], exceptuar: ScrapeFicha | None = None) -> None:
    """Marca VACIO las hermanas aún reintentables; no toca DONE/VACIO ni ERROR agotado."""
    for hermana in hermanas:
        if hermana is exceptuar:
            continue
        if _es_procesable(hermana):
            _marcar_vacio(hermana)


async def procesar_item_ahdss(session: AsyncSession, client: ScraperClient, item: ScrapeFicha) -> None:
    """Resuelve un ID global de AHDSS (Gipuzkoa) como una sola unidad de trabajo.

    En Méndez Mende cada ``id_registro`` pertenece a UN sacramento. La cola
    tiene 3 filas por ID (auditoría: 1 DONE + 2 VACIO, o 3 VACIO si es un
    hueco); el worker corta el HTTP en cuanto sabe cuál es. No hace falta
    borrar ni re-sembrar las ~5.9M filas ya encoladas.

    Verificación (sólo conteos, nunca DELETE)::

        SELECT id_registro,
               count(*) FILTER (WHERE status = 'DONE')  AS n_done,
               count(*) FILTER (WHERE status = 'VACIO') AS n_vacio
        FROM siga.scrape_fichas
        WHERE archivo = 'AHDSS'
        GROUP BY id_registro
        HAVING count(*) FILTER (WHERE status IN ('PENDING', 'ERROR')) = 0
           AND NOT (
               (count(*) FILTER (WHERE status = 'DONE') = 1
                AND count(*) FILTER (WHERE status = 'VACIO') = 2)
               OR count(*) FILTER (WHERE status = 'VACIO') = 3
           )
        LIMIT 50;
    """
    hermanas = await _cargar_hermanas(session, item.archivo, item.id_registro)
    if not hermanas:
        hermanas = [item]
    por_sacramento = {h.sacramento: h for h in hermanas}
    if item.sacramento not in por_sacramento:
        por_sacramento[item.sacramento] = item
        hermanas.append(item)

    if any(h.status == FichaStatus.DONE for h in hermanas):
        _cerrar_hermanas_pendientes(hermanas)
        await session.commit()
        return

    for sacramento in _orden_intentos_ahdss(item.sacramento):
        hermana = por_sacramento.get(sacramento)
        if hermana is None or not _es_procesable(hermana):
            continue
        try:
            html = await fetch_ficha_html_ahdss(client, sacramento, item.id_registro)
            ficha = parse_ficha_ahdss(html, item.id_registro, sacramento)
            if ficha is None:
                _marcar_vacio(hermana)
                continue
            await _persistir_ficha_ok(session, client, hermana, html, ficha)
        except Exception as exc:  # noqa: BLE001 - se persiste el error para poder reanudar
            _marcar_error(hermana, exc)
            await session.commit()
            return

        _cerrar_hermanas_pendientes(hermanas, exceptuar=hermana)
        await session.commit()
        return

    await session.commit()


async def procesar_item(session: AsyncSession, client: ScraperClient, item: ScrapeFicha) -> None:
    if item.archivo == Archivo.AHDSS:
        await procesar_item_ahdss(session, client, item)
        return

    try:
        html = await fetch_ficha_html(client, item.sacramento, item.id_registro)
        ficha = parse_ficha(html, item.id_registro)

        if ficha is None:
            # Hueco en la numeración del portal (normal en archivos que se
            # recorren por rango de ID, p.ej. AHDV-GEAH): no es un error.
            _marcar_vacio(item)
            await session.commit()
            return

        await _persistir_ficha_ok(session, client, item, html, ficha)
    except Exception as exc:  # noqa: BLE001 - se persiste el error para poder reanudar
        _marcar_error(item, exc)

    await session.commit()


async def procesar_fichas(
    session: AsyncSession,
    clients: ScraperClient | dict[Archivo, ScraperClient],
    limite: int | None = None,
    archivo: Archivo | None = None,
) -> int:
    """Procesa fichas `pending` (o `error` con pocos reintentos) en lotes.

    Nunca carga la cola entera: cada round-trip pide como máximo
    ``LOTE_MAX_FILAS`` filas. ``limite`` cuenta unidades de trabajo: 1 fila
    en AHEB-BEHA/AHDV-GEAH, 1 ``id_registro`` distinto en AHDSS (las
    hermanas se resuelven juntas y se saltan si ya no están PENDING/ERROR).

    ``clients`` puede ser un único :class:`ScraperClient` (comportamiento
    histórico, asume AHEB-BEHA) o un diccionario ``{archivo: cliente}`` para
    procesar la cola compartida con varios archivos a la vez, cada ficha con
    el cliente (dominio/codificación) que le corresponde.
    """
    clientes_por_archivo = clients if isinstance(clients, dict) else {Archivo.AHEB_BEHA: clients}

    procesados = 0
    while True:
        remaining = None if limite is None else limite - procesados
        if remaining is not None and remaining <= 0:
            break

        items = await _cargar_pendientes(session, archivo, _filas_a_cargar(remaining, archivo))
        if not items:
            break

        vistos_ahdss: set[int] = set()
        trabajo_en_lote = 0
        for item in items:
            if remaining is not None and procesados >= limite:
                break
            if item.archivo == Archivo.AHDSS:
                if item.id_registro in vistos_ahdss:
                    continue
                if not _es_procesable(item):
                    continue
            elif not _es_procesable(item):
                continue

            cliente = clientes_por_archivo.get(item.archivo)
            if cliente is None:
                logger.warning("Sin cliente configurado para archivo %s; se omite ficha %s", item.archivo, item.id)
                continue

            await procesar_item(session, cliente, item)
            if item.archivo == Archivo.AHDSS:
                vistos_ahdss.add(item.id_registro)
            procesados += 1
            trabajo_en_lote += 1

        if trabajo_en_lote == 0:
            break

    return procesados
