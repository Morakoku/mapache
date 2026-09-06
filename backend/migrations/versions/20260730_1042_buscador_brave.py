"""buscador web configurable: Google Custom Search o Brave

Hasta ahora el único buscador posible era Google Custom Search, con sus 100
consultas gratis al día. Brave da 2.000 al mes y no necesita crear un motor,
así que la elección pasa a ser un dato de configuración.

Por defecto GOOGLE_CSE: quien ya tenga la clave puesta sigue igual sin tocar
nada. El ENUM se crea a mano antes de la columna; dentro de `add_column`
funciona al subir pero deja el tipo huérfano al bajar.

Revision ID: b41d7e9c2a58
Revises: 7aa0a3ea011c
Create Date: 2026-07-30 10:42:11.184023
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b41d7e9c2a58"
down_revision: str | None = "7aa0a3ea011c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


serp_provider = postgresql.ENUM("GOOGLE_CSE", "BRAVE", name="serp_provider")


def upgrade() -> None:
    serp_provider.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "app_settings",
        sa.Column(
            "serp_provider",
            postgresql.ENUM(name="serp_provider", create_type=False),
            server_default="GOOGLE_CSE",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "serp_provider")

    serp_provider.drop(op.get_bind(), checkfirst=True)
