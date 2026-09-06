"""fase 11 requisitos de contacto y proveedor de IA configurable

Dos cosas independientes que caen en la misma migración:

- `searches.require_*`: exigir teléfono, web o email al descubrir. Por defecto
  en falso para no cambiar el comportamiento de las búsquedas que ya existen.
- `app_settings.ai_provider` + `ai_api_key_enc`: elegir entre Claude, OpenAI,
  DeepSeek o Kimi desde la interfaz, con la clave cifrada en la base.

El ENUM se crea a mano antes de la columna: dentro de `add_column` funciona al
subir pero deja el tipo huérfano al bajar.

Revision ID: f3c65e437b4c
Revises: 3004cd69e632
Create Date: 2026-07-28 23:57:33.903222
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3c65e437b4c"
down_revision: str | None = "3004cd69e632"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ai_provider = postgresql.ENUM(
    "ANTHROPIC",
    "OPENAI",
    "DEEPSEEK",
    "KIMI",
    name="ai_provider",
)


def upgrade() -> None:
    ai_provider.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "app_settings",
        sa.Column(
            "ai_provider",
            postgresql.ENUM(name="ai_provider", create_type=False),
            server_default="ANTHROPIC",
            nullable=False,
        ),
    )
    op.add_column("app_settings", sa.Column("ai_api_key_enc", sa.Text(), nullable=True))

    for columna in ("require_phone", "require_website", "require_email"):
        op.add_column(
            "searches",
            sa.Column(columna, sa.Boolean(), server_default=sa.text("false"), nullable=False),
        )


def downgrade() -> None:
    op.drop_column("searches", "require_email")
    op.drop_column("searches", "require_website")
    op.drop_column("searches", "require_phone")
    op.drop_column("app_settings", "ai_api_key_enc")
    op.drop_column("app_settings", "ai_provider")

    ai_provider.drop(op.get_bind(), checkfirst=True)
