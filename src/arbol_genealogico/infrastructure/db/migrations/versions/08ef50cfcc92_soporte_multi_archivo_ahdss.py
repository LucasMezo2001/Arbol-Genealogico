"""soporte_multi_archivo_ahdss

Revision ID: 08ef50cfcc92
Revises: 62dabef5cc75
Create Date: 2026-08-15 12:38:40.348597

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '08ef50cfcc92'
down_revision: Union[str, Sequence[str], None] = '62dabef5cc75'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # AHDSS (Gipuzkoa, portal de Méndez Mende): a diferencia de la
    # migración anterior (AHDV-GEAH), no hace falta tocar columnas ni
    # claves -ya existen desde el soporte multi-archivo-, sólo añadir el
    # valor al enum. Autogenerate no detecta altas de valor en enums de
    # Postgres: hay que hacerlo a mano con ALTER TYPE.
    op.execute("ALTER TYPE siga.archivo_enum ADD VALUE IF NOT EXISTS 'AHDSS'")


def downgrade() -> None:
    """Downgrade schema."""
    # No existe "ALTER TYPE ... DROP VALUE": revertir de verdad requiere
    # recrear el tipo `archivo_enum` a mano (ver migración 62dabef5cc75),
    # y sólo es seguro si no hay ninguna fila usando 'AHDSS'.
    pass
