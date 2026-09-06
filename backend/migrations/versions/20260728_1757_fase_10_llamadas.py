"""fase 10 llamadas

Guiones de llamada (`call_scripts`) y registro de llamadas (`call_logs`).

Los dos ENUM nuevos se crean a mano antes de las tablas: la autogeneración los
declara dentro de `create_table`, lo que funciona al subir pero deja el tipo
huérfano al bajar. `activity_type` gana un valor nuevo con ALTER TYPE, que en
PostgreSQL no se puede deshacer sin recrear el tipo entero — por eso el
downgrade lo deja donde está y solo borra las tablas.

Revision ID: 3004cd69e632
Revises: 0b31298f593b
Create Date: 2026-07-28 17:57:12.877946
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3004cd69e632"
down_revision: str | None = "0b31298f593b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


call_script_type = postgresql.ENUM(
    "COLD_FIRST",
    "GATEKEEPER",
    "INBOUND",
    "FOLLOW_UP",
    "MEETING",
    name="call_script_type",
)
call_outcome = postgresql.ENUM(
    "NO_ANSWER",
    "VOICEMAIL",
    "GATEKEEPER",
    "WRONG_NUMBER",
    "CALLBACK",
    "NOT_INTERESTED",
    "INTERESTED",
    "MEETING_SCHEDULED",
    "DO_NOT_CALL",
    name="call_outcome",
)


def upgrade() -> None:
    bind = op.get_bind()
    call_script_type.create(bind, checkfirst=True)
    call_outcome.create(bind, checkfirst=True)

    # Una llamada registrada es una actividad más en la ficha del prospecto.
    op.execute("ALTER TYPE activity_type ADD VALUE IF NOT EXISTS 'CALL_LOGGED'")

    op.create_table(
        "call_scripts",
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "script_type",
            postgresql.ENUM(name="call_script_type", create_type=False),
            nullable=False,
        ),
        sa.Column("service_id", sa.UUID(), nullable=True),
        sa.Column("opening", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column(
            "questions",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("value_pitch", sa.Text(), nullable=True),
        sa.Column("close", sa.Text(), nullable=True),
        sa.Column(
            "objections",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("times_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_call_scripts_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_call_scripts")),
        sa.UniqueConstraint(
            "owner_id",
            "name",
            name="uq_call_scripts_owner_name",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(op.f("ix_call_scripts_owner_id"), "call_scripts", ["owner_id"], unique=False)
    op.create_index(
        "ix_call_scripts_type",
        "call_scripts",
        ["owner_id", "script_type", "is_active"],
        unique=False,
    )

    op.create_table(
        "call_logs",
        sa.Column("lead_id", sa.UUID(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column("script_id", sa.UUID(), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column(
            "outcome",
            postgresql.ENUM(name="call_outcome", create_type=False),
            nullable=False,
        ),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("attempt", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            name=op.f("fk_call_logs_contact_id_contacts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name=op.f("fk_call_logs_lead_id_leads"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["script_id"],
            ["call_scripts.id"],
            name=op.f("fk_call_logs_script_id_call_scripts"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_call_logs")),
    )
    op.create_index("ix_call_logs_lead", "call_logs", ["lead_id", "occurred_at"], unique=False)
    op.create_index(
        "ix_call_logs_outcome", "call_logs", ["owner_id", "outcome", "occurred_at"], unique=False
    )
    op.create_index(op.f("ix_call_logs_owner_id"), "call_logs", ["owner_id"], unique=False)

    # Semilla de los cinco guiones de fábrica. Van en la migración por lo
    # mismo que las etapas: la pantalla de llamada sin ningún guion no sirve
    # para nada, y escribir cinco desde cero antes de la primera llamada es
    # una barrera de entrada absurda.
    from app.models.call import DEFAULT_SCRIPTS

    op.bulk_insert(
        sa.table(
            "call_scripts",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("name", sa.String),
            sa.column("script_type", postgresql.ENUM(name="call_script_type", create_type=False)),
            sa.column("opening", sa.Text),
            sa.column("context", sa.Text),
            sa.column("questions", postgresql.ARRAY(sa.Text)),
            sa.column("value_pitch", sa.Text),
            sa.column("close", sa.Text),
            sa.column("objections", postgresql.JSONB),
            sa.column("is_active", sa.Boolean),
            sa.column("is_system", sa.Boolean),
        ),
        [
            {
                "id": uuid.uuid4(),
                "name": script["name"],
                "script_type": script["script_type"].value,
                "opening": script["opening"],
                "context": script.get("context"),
                "questions": script.get("questions", []),
                "value_pitch": script.get("value_pitch"),
                "close": script.get("close"),
                "objections": script.get("objections", []),
                "is_active": True,
                "is_system": True,
            }
            for script in DEFAULT_SCRIPTS
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_call_logs_owner_id"), table_name="call_logs")
    op.drop_index("ix_call_logs_outcome", table_name="call_logs")
    op.drop_index("ix_call_logs_lead", table_name="call_logs")
    op.drop_table("call_logs")
    op.drop_index("ix_call_scripts_type", table_name="call_scripts")
    op.drop_index(op.f("ix_call_scripts_owner_id"), table_name="call_scripts")
    op.drop_table("call_scripts")

    bind = op.get_bind()
    call_outcome.drop(bind, checkfirst=True)
    call_script_type.drop(bind, checkfirst=True)
    # `CALL_LOGGED` se queda en `activity_type`: quitar un valor de un ENUM en
    # PostgreSQL obliga a recrear el tipo y reescribir la tabla `activities`.
    # Un valor de más no molesta a nadie; una migración de bajada que reescribe
    # una tabla grande, sí.