from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMA = "siga"


class Base(DeclarativeBase):
    """Base declarativa; todas las tablas viven en el esquema ``siga``."""

    metadata = MetaData(schema=SCHEMA)


class Sacramento(str, enum.Enum):
    BAUTISMO = "bautismo"
    MATRIMONIO = "matrimonio"
    DIFUNTO = "difunto"


class Archivo(str, enum.Enum):
    """Archivo diocesano de origen. Los IDs de registro (``id_bautismo``,
    etc.) los asigna cada portal de forma independiente, así que colisionan
    entre archivos: por eso ``archivo`` forma parte de la clave de los
    registros sacramentales y de la cola de scraping.

    AHEB_BEHA (Bizkaia) y AHDV_GEAH (Álava) comparten la plataforma
    SIGA-AKIS. AHDSS (Gipuzkoa, portal de Méndez Mende) es una plataforma
    distinta (Yii/Arinka): mismo modelo de datos, pero cliente HTTP, parser
    y descubrimiento de rango propios (ver ``infrastructure/scraper`` y
    ``features/scraping/rango.py``)."""

    AHEB_BEHA = "aheb_beha"
    AHDV_GEAH = "ahdv_geah"
    AHDSS = "ahdss"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class FichaStatus(str, enum.Enum):
    PENDING = "pending"
    DONE = "done"
    ERROR = "error"
    VACIO = "vacio"
    """El ID no corresponde a ningún registro real (hueco en la numeración
    del portal): es un resultado terminal esperado, no un error a reintentar.
    """


def _archivo_column(**kwargs: object):  # noqa: ANN201 - devuelve lo que da mapped_column
    """Columna ``archivo`` común a las tablas afectadas por el origen del dato.

    Usa ``server_default`` (no sólo default de la app) porque hay procesos de
    scraping antiguos en ejecución cuya definición de modelos en memoria no
    incluye esta columna: sus INSERT deben seguir funcionando gracias al
    valor por defecto que pone Postgres.
    """
    opciones: dict[str, object] = {
        "nullable": False,
        "server_default": Archivo.AHEB_BEHA.name,
        "index": not kwargs.get("primary_key"),
    }
    opciones.update(kwargs)
    return mapped_column(Enum(Archivo, name="archivo_enum", schema=SCHEMA), **opciones)


class Localidad(Base):
    """Localidad/municipio de una parroquia.

    ``id_localidad`` es un identificador propio (no necesariamente el ID que
    usa el portal de origen): para AHEB-BEHA coincide con el ID del ``
    <select>`` de búsqueda; para AHDV-GEAH (que no expone un ID estable en la
    ficha) se genera automáticamente a partir de un rango alto reservado
    (>=100000) para no colisionar con AHEB-BEHA.
    """

    __tablename__ = "localidades"
    __table_args__ = (UniqueConstraint("archivo", "nombre", name="uq_localidad_archivo_nombre"),)

    id_localidad: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    archivo: Mapped[Archivo] = _archivo_column()
    nombre: Mapped[str] = mapped_column(String(200), nullable=False)


class Fondo(Base):
    """NOTA: ``codigo`` sigue siendo único a secas (sin ``archivo``) para no
    romper el ``ON CONFLICT`` de procesos de scraping ya en marcha. El riesgo
    de colisión de códigos de fondo entre archivos distintos es bajo (cada
    diócesis usa su propia numeración de fondos parroquiales) y se acepta
    como limitación conocida; ``archivo`` queda como columna informativa."""

    __tablename__ = "fondos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    archivo: Mapped[Archivo] = _archivo_column()
    codigo: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)
    descripcion: Mapped[str | None] = mapped_column(Text)


class Parroquia(Base):
    """Ver nota de :class:`Fondo` sobre por qué ``archivo`` no forma parte de
    la restricción de unicidad."""

    __tablename__ = "parroquias"
    __table_args__ = (UniqueConstraint("codigo", "nombre", name="uq_parroquia_codigo_nombre"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    archivo: Mapped[Archivo] = _archivo_column()
    codigo: Mapped[str | None] = mapped_column(String(100), index=True)
    nombre: Mapped[str | None] = mapped_column(String(300))
    id_localidad: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.localidades.id_localidad"), index=True)
    diocesis: Mapped[str | None] = mapped_column(String(200))
    territorio_historico: Mapped[str | None] = mapped_column(String(200))
    localidad_texto: Mapped[str | None] = mapped_column(String(300))

    localidad: Mapped[Localidad | None] = relationship()


class Persona(Base):
    """La ficha de detalle no expone el sexo de forma fiable, así que la
    identidad de una persona se basa sólo en nombre + apellidos. Nótese que en
    Postgres NULL nunca es igual a NULL, por lo que dos personas con datos
    parcialmente desconocidos no se deduplican entre sí (comportamiento
    aceptado: no hay suficiente información para asegurarlo)."""

    __tablename__ = "personas"
    __table_args__ = (UniqueConstraint("nombre", "apellido1", "apellido2", name="uq_persona_identidad"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str | None] = mapped_column(String(200), index=True)
    apellido1: Mapped[str | None] = mapped_column(String(200), index=True)
    apellido2: Mapped[str | None] = mapped_column(String(200), index=True)
    sexo: Mapped[str | None] = mapped_column(String(1))


class RegistroBase:
    """Columnas comunes a bautismos, matrimonios y defunciones.

    ``archivo`` forma parte de la clave primaria junto al ID del registro
    (``id_bautismo``, etc.) porque cada archivo diocesano numera sus
    registros de forma independiente y esos IDs colisionan entre archivos.
    """

    archivo: Mapped[Archivo] = _archivo_column(primary_key=True)
    fecha: Mapped[dt.date | None] = mapped_column(index=True)
    comentarios: Mapped[str | None] = mapped_column(Text)
    codigo_referencia: Mapped[str | None] = mapped_column(String(200))
    signatura: Mapped[str | None] = mapped_column(String(200))
    sig_antigua: Mapped[str | None] = mapped_column(String(200))
    sig_microfilm: Mapped[str | None] = mapped_column(String(200))
    sig_digital: Mapped[str | None] = mapped_column(String(200))
    sig_digital_libro: Mapped[str | None] = mapped_column(String(200))
    pagina: Mapped[str | None] = mapped_column(String(100))
    fechas_libro: Mapped[str | None] = mapped_column(String(200))
    html_sha256: Mapped[str | None] = mapped_column(String(64))
    scraped_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Bautismo(RegistroBase, Base):
    __tablename__ = "bautismos"

    id_bautismo: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    id_persona: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.personas.id"), index=True)
    id_padre: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.personas.id"), index=True)
    id_madre: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.personas.id"), index=True)
    id_parroquia: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.parroquias.id"), index=True)
    id_localidad: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.localidades.id_localidad"), index=True)
    id_fondo: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.fondos.id"), index=True)

    persona: Mapped[Persona | None] = relationship(foreign_keys=[id_persona])
    padre: Mapped[Persona | None] = relationship(foreign_keys=[id_padre])
    madre: Mapped[Persona | None] = relationship(foreign_keys=[id_madre])
    parroquia: Mapped[Parroquia | None] = relationship()


class Matrimonio(RegistroBase, Base):
    __tablename__ = "matrimonios"

    id_matrimonio: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    id_esposo: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.personas.id"), index=True)
    id_esposa: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.personas.id"), index=True)
    id_parroquia: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.parroquias.id"), index=True)
    id_localidad: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.localidades.id_localidad"), index=True)
    id_fondo: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.fondos.id"), index=True)

    esposo: Mapped[Persona | None] = relationship(foreign_keys=[id_esposo])
    esposa: Mapped[Persona | None] = relationship(foreign_keys=[id_esposa])
    parroquia: Mapped[Parroquia | None] = relationship()


class Defuncion(RegistroBase, Base):
    __tablename__ = "defunciones"

    id_difunto: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    id_persona: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.personas.id"), index=True)
    id_conyuge: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.personas.id"))
    id_padre: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.personas.id"))
    id_madre: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.personas.id"))
    id_parroquia: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.parroquias.id"), index=True)
    id_localidad: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.localidades.id_localidad"), index=True)
    id_fondo: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.fondos.id"), index=True)
    edad: Mapped[str | None] = mapped_column(String(50))

    persona: Mapped[Persona | None] = relationship(foreign_keys=[id_persona])
    parroquia: Mapped[Parroquia | None] = relationship()


class ScrapeJob(Base):
    """Unidad de trabajo: un listado paginado para (sacramento, localidad, año).

    Usa la "búsqueda especial" del portal (sólo localidad + rango de fechas,
    sin dato de persona), que es la única que devuelve el 100% de los
    registros sin necesitar un apellido/comodín válido.
    """

    __tablename__ = "scrape_jobs"
    __table_args__ = (UniqueConstraint("sacramento", "id_localidad", "anio_ini", "anio_fin", name="uq_scrape_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    archivo: Mapped[Archivo] = _archivo_column()
    sacramento: Mapped[Sacramento] = mapped_column(Enum(Sacramento, name="sacramento_enum", schema=SCHEMA))
    id_localidad: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.localidades.id_localidad"), index=True)
    anio_ini: Mapped[int] = mapped_column(Integer, index=True)
    anio_fin: Mapped[int] = mapped_column(Integer)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status_enum", schema=SCHEMA), default=JobStatus.PENDING, index=True
    )
    total_registros: Mapped[int | None] = mapped_column(Integer)
    paginas_totales: Mapped[int | None] = mapped_column(Integer)
    ultima_pagina: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    iniciado_en: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finalizado_en: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    creado_en: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScrapeFicha(Base):
    """Cola de fichas de detalle pendientes de descargar/parsear."""

    __tablename__ = "scrape_fichas"
    __table_args__ = (UniqueConstraint("archivo", "id_registro", "sacramento", name="uq_scrape_ficha"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    archivo: Mapped[Archivo] = _archivo_column()
    id_registro: Mapped[int] = mapped_column(Integer, index=True)
    sacramento: Mapped[Sacramento] = mapped_column(Enum(Sacramento, name="sacramento_enum", schema=SCHEMA))
    id_localidad: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.localidades.id_localidad"), index=True)
    """``None`` cuando el archivo no permite conocer la localidad antes de
    descargar la ficha (p.ej. AHDV-GEAH, que se recorre por rango de IDs sin
    búsqueda previa); se completa al procesar la ficha si el dato aparece."""
    status: Mapped[FichaStatus] = mapped_column(
        Enum(FichaStatus, name="ficha_status_enum", schema=SCHEMA), default=FichaStatus.PENDING, index=True
    )
    retries: Mapped[int] = mapped_column(Integer, default=0)
    next_try_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    scraped_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    creado_en: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


__all__ = [
    "Base",
    "SCHEMA",
    "Archivo",
    "Sacramento",
    "JobStatus",
    "FichaStatus",
    "Localidad",
    "Fondo",
    "Parroquia",
    "Persona",
    "Bautismo",
    "Matrimonio",
    "Defuncion",
    "ScrapeJob",
    "ScrapeFicha",
]
