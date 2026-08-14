from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from arbol_genealogico.infrastructure.db.models import (
    Bautismo,
    Defuncion,
    FichaStatus,
    Fondo,
    Matrimonio,
    Parroquia,
    Persona,
    Sacramento,
    ScrapeFicha,
)
from arbol_genealogico.infrastructure.scraper.client import ScraperClient
from arbol_genealogico.infrastructure.scraper.endpoints import fetch_ficha_html
from arbol_genealogico.infrastructure.scraper.parser import FichaCompleta, PersonaDatos, parse_ficha

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


async def _upsert_parroquia(session: AsyncSession, ficha: FichaCompleta, id_localidad: int) -> int | None:
    if ficha.parroquia_nombre is None and ficha.parroquia_codigo is None:
        return None
    stmt = (
        pg_insert(Parroquia)
        .values(
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


async def _upsert_fondo(session: AsyncSession, ficha: FichaCompleta) -> int | None:
    if ficha.fondo_codigo is None:
        return None
    stmt = (
        pg_insert(Fondo)
        .values(codigo=ficha.fondo_codigo, descripcion=ficha.fondo_descripcion)
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
        html = await fetch_ficha_html(client, item.sacramento, item.id_registro)
        ficha = parse_ficha(html, item.id_registro)

        id_fondo = await _upsert_fondo(session, ficha)
        id_parroquia = await _upsert_parroquia(session, ficha, item.id_localidad)
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
            "id_localidad": item.id_localidad,
            "html_sha256": client.sha256(html),
        }

        if item.sacramento == Sacramento.BAUTISMO:
            id_persona = await _upsert_persona(session, ficha.persona)
            id_padre = await _upsert_persona(session, ficha.padre)
            id_madre = await _upsert_persona(session, ficha.madre)
            stmt = (
                pg_insert(Bautismo)
                .values(
                    id_bautismo=item.id_registro,
                    id_persona=id_persona,
                    id_padre=id_padre,
                    id_madre=id_madre,
                    **campos_comunes,
                )
                .on_conflict_do_update(index_elements=[Bautismo.id_bautismo], set_=campos_comunes)
            )
        elif item.sacramento == Sacramento.MATRIMONIO:
            id_esposo = await _upsert_persona(session, ficha.esposo)
            id_esposa = await _upsert_persona(session, ficha.esposa)
            stmt = (
                pg_insert(Matrimonio)
                .values(
                    id_matrimonio=item.id_registro,
                    id_esposo=id_esposo,
                    id_esposa=id_esposa,
                    **campos_comunes,
                )
                .on_conflict_do_update(index_elements=[Matrimonio.id_matrimonio], set_=campos_comunes)
            )
        else:
            id_persona = await _upsert_persona(session, ficha.persona)
            id_conyuge = await _upsert_persona(session, ficha.conyuge)
            id_padre = await _upsert_persona(session, ficha.padre)
            id_madre = await _upsert_persona(session, ficha.madre)
            stmt = (
                pg_insert(Defuncion)
                .values(
                    id_difunto=item.id_registro,
                    id_persona=id_persona,
                    id_conyuge=id_conyuge,
                    id_padre=id_padre,
                    id_madre=id_madre,
                    edad=ficha.edad,
                    **campos_comunes,
                )
                .on_conflict_do_update(
                    index_elements=[Defuncion.id_difunto], set_={**campos_comunes, "edad": ficha.edad}
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


async def procesar_fichas(session: AsyncSession, client: ScraperClient, limite: int | None = None) -> int:
    """Procesa fichas `pending` (o `error` con pocos reintentos) una a una."""
    stmt = (
        select(ScrapeFicha)
        .where(ScrapeFicha.status.in_([FichaStatus.PENDING, FichaStatus.ERROR]))
        .where(ScrapeFicha.retries < 5)
        .order_by(ScrapeFicha.id)
    )
    if limite is not None:
        stmt = stmt.limit(limite)

    items = (await session.execute(stmt)).scalars().all()
    procesados = 0
    for item in items:
        await procesar_item(session, client, item)
        procesados += 1
    return procesados
