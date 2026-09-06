"""fase 7 secuencias

Añade la plantilla de secuencia (`sequences` + `sequence_steps`) y sus
instancias programadas (`follow_ups`), más el enlace desde el correo enviado
hacia el seguimiento que lo originó.

El orden de creación importa: `follow_ups` referencia a `sequences`, así que
la autogeneración (que las creó al revés, por orden alfabético) no funcionaba.

Revision ID: bf6d7b8ab808
Revises: 8c1a4f6d2b73
Create Date: 2026-07-28 10:08:36.683793
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "bf6d7b8ab808"
down_revision: str | None = "8c1a4f6d2b73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sequences",
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("service_id", sa.UUID(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("stop_on_reply", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("stop_on_click", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("stop_on_meeting", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("max_steps", sa.SmallInteger(), server_default="3", nullable=False),
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
            name=op.f("fk_sequences_service_id_services"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sequences")),
    )
    op.create_index(op.f("ix_sequences_owner_id"), "sequences", ["owner_id"], unique=False)
    op.create_index(
        "uq_sequences_owner_name",
        "sequences",
        ["owner_id", sa.literal_column("lower(name)")],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    op.create_table(
        "sequence_steps",
        sa.Column("sequence_id", sa.UUID(), nullable=False),
        sa.Column("step_number", sa.SmallInteger(), nullable=False),
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("delay_days", sa.SmallInteger(), server_default="3", nullable=False),
        sa.Column("delay_hours", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("condition", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("send_window_start", sa.Time(), nullable=True),
        sa.Column("send_window_end", sa.Time(), nullable=True),
        sa.Column("skip_weekends", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["sequence_id"],
            ["sequences.id"],
            name=op.f("fk_sequence_steps_sequence_id_sequences"),
            ondelete="CASCADE",
        ),
        # RESTRICT: borrar una plantilla usada por una secuencia activa
        # dejaría pasos que no se pueden ejecutar.
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["email_templates.id"],
            name=op.f("fk_sequence_steps_template_id_email_templates"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sequence_steps")),
        sa.UniqueConstraint("sequence_id", "step_number", name="uq_sequence_steps_number"),
    )

    op.create_table(
        "follow_ups",
        sa.Column("lead_id", sa.UUID(), nullable=False),
        sa.Column("sequence_id", sa.UUID(), nullable=True),
        sa.Column("sequence_step", sa.SmallInteger(), nullable=True),
        sa.Column("template_id", sa.UUID(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("sent_email_id", sa.UUID(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("skip_reason", sa.String(length=40), nullable=True),
        sa.Column("attempts", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("is_manual", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
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
            ["lead_id"], ["leads.id"], name=op.f("fk_follow_ups_lead_id_leads"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["sent_email_id"],
            ["email_messages.id"],
            name=op.f("fk_follow_ups_sent_email_id_email_messages"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sequence_id"],
            ["sequences.id"],
            name=op.f("fk_follow_ups_sequence_id_sequences"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["email_templates.id"],
            name=op.f("fk_follow_ups_template_id_email_templates"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_follow_ups")),
    )
    op.create_index(
        "ix_follow_ups_due",
        "follow_ups",
        ["scheduled_at"],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index("ix_follow_ups_lead", "follow_ups", ["lead_id", "scheduled_at"], unique=False)
    op.create_index(op.f("ix_follow_ups_owner_id"), "follow_ups", ["owner_id"], unique=False)
    # Un paso por prospecto: inscribir dos veces por error no duplica correos.
    op.create_index(
        "uq_follow_ups_lead_step",
        "follow_ups",
        ["lead_id", "sequence_id", "sequence_step"],
        unique=True,
        postgresql_where=sa.text("sequence_id IS NOT NULL AND status = 'PENDING'"),
    )

    op.add_column("email_messages", sa.Column("follow_up_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_email_messages_follow_up_id_follow_ups"),
        "email_messages",
        "follow_ups",
        ["follow_up_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_email_messages_follow_up_id_follow_ups"), "email_messages", type_="foreignkey"
    )
    op.drop_column("email_messages", "follow_up_id")

    op.drop_index(
        "uq_follow_ups_lead_step",
        table_name="follow_ups",
        postgresql_where=sa.text("sequence_id IS NOT NULL AND status = 'PENDING'"),
    )
    op.drop_index(op.f("ix_follow_ups_owner_id"), table_name="follow_ups")
    op.drop_index("ix_follow_ups_lead", table_name="follow_ups")
    op.drop_index(
        "ix_follow_ups_due", table_name="follow_ups", postgresql_where=sa.text("status = 'PENDING'")
    )
    op.drop_table("follow_ups")

    op.drop_table("sequence_steps")
    op.drop_index(
        "uq_sequences_owner_name", table_name="sequences", postgresql_nulls_not_distinct=True
    )
    op.drop_index(op.f("ix_sequences_owner_id"), table_name="sequences")
    op.drop_table("sequences")
