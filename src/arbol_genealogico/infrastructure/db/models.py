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


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class FichaStatus(str, enum.Enum):
    PENDING = "pending"
    DONE = "done"
    ERROR = "error"


class Localidad(Base):
    __tablename__ = "localidades"

    id_localidad: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    nombre: Mapped[str] = mapped_column(String(200), nullable=False)


class Fondo(Base):
    __tablename__ = "fondos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)
    descripcion: Mapped[str | None] = mapped_column(Text)


class Parroquia(Base):
    __tablename__ = "parroquias"
    __table_args__ = (UniqueConstraint("codigo", "nombre", name="uq_parroquia_codigo_nombre"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
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
    """Columnas comunes a bautismos, matrimonios y defunciones."""

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
    __table_args__ = (UniqueConstraint("id_registro", "sacramento", name="uq_scrape_ficha"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_registro: Mapped[int] = mapped_column(Integer, index=True)
    sacramento: Mapped[Sacramento] = mapped_column(Enum(Sacramento, name="sacramento_enum", schema=SCHEMA))
    id_localidad: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.localidades.id_localidad"), index=True)
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
