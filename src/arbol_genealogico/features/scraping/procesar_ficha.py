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


def _parse_fecha(raw: str | None) -> dt.date | None:
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw[:10])
    except ValueError:
        return None


async def procesar_item(session: AsyncSession, client: ScraperClient, item: ScrapeFicha) -> None:
    try:
        if item.archivo == Archivo.AHDSS:
            # Portal ajeno a SIGA-AKIS: fetch/parse propios (ver rango.py).
            html = await fetch_ficha_html_ahdss(client, item.sacramento, item.id_registro)
            ficha = parse_ficha_ahdss(html, item.id_registro, item.sacramento)
        else:
            html = await fetch_ficha_html(client, item.sacramento, item.id_registro)
            ficha = parse_ficha(html, item.id_registro)

        if ficha is None:
            # Hueco en la numeración del portal (normal en archivos que se
            # recorren por rango de ID, p.ej. AHDV-GEAH/AHDSS) o, en AHDSS,
            # un ID que pertenece a otro sacramento: no es un error.
            item.status = FichaStatus.VACIO
            item.scraped_at = dt.datetime.now(dt.UTC)
            item.error = None
            await session.commit()
            return

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
                .on_conflict_do_update(
                    index_elements=[Matrimonio.archivo, Matrimonio.id_matrimonio], set_=campos_comunes
                )
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
    except Exception as exc:  # noqa: BLE001 - se persiste el error para poder reanudar
        logger.exception("Error procesando scrape_ficha %s/%s", item.sacramento, item.id_registro)
        item.status = FichaStatus.ERROR
        item.retries += 1
        item.error = str(exc)[:MAX_ERROR_LEN]

    await session.commit()


async def procesar_fichas(
    session: AsyncSession,
    clients: ScraperClient | dict[Archivo, ScraperClient],
    limite: int | None = None,
    archivo: Archivo | None = None,
) -> int:
    """Procesa fichas `pending` (o `error` con pocos reintentos) una a una.

    ``clients`` puede ser un único :class:`ScraperClient` (comportamiento
    histórico, asume AHEB-BEHA) o un diccionario ``{archivo: cliente}`` para
    procesar la cola compartida con varios archivos a la vez, cada ficha con
    el cliente (dominio/codificación) que le corresponde.
    """
    clientes_por_archivo = clients if isinstance(clients, dict) else {Archivo.AHEB_BEHA: clients}

    stmt = (
        select(ScrapeFicha)
        .where(ScrapeFicha.status.in_([FichaStatus.PENDING, FichaStatus.ERROR]))
        .where(ScrapeFicha.retries < 5)
        .order_by(ScrapeFicha.id)
    )
    if archivo is not None:
        stmt = stmt.where(ScrapeFicha.archivo == archivo)
    if limite is not None:
        stmt = stmt.limit(limite)

    items = (await session.execute(stmt)).scalars().all()
    procesados = 0
    for item in items:
        cliente = clientes_por_archivo.get(item.archivo)
        if cliente is None:
            logger.warning("Sin cliente configurado para archivo %s; se omite ficha %s", item.archivo, item.id)
            continue
        await procesar_item(session, cliente, item)
        procesados += 1
    return procesados
