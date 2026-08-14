from __future__ import annotations

import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from arbol_genealogico.infrastructure.db.models import Localidad
from arbol_genealogico.infrastructure.db.seed_data import LOCALIDADES

logger = logging.getLogger(__name__)


async def seed_localidades(session: AsyncSession) -> int:
    """Inserta (o actualiza) las 115 localidades de referencia. Idempotente."""
    if not LOCALIDADES:
        return 0

    stmt = pg_insert(Localidad).values(
        [{"id_localidad": id_localidad, "nombre": nombre} for id_localidad, nombre in LOCALIDADES]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Localidad.id_localidad],
        set_={"nombre": stmt.excluded.nombre},
    )
    await session.execute(stmt)
    await session.commit()
    logger.info("Seed de localidades aplicado: %d filas", len(LOCALIDADES))
    return len(LOCALIDADES)
