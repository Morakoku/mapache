"""fase 3 crm

Revision ID: 99360f8ac979
Revises: 1dbee674b5e4
Create Date: 2026-07-27 17:36:56.553341
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
import uuid
from sqlalchemy.dialects import postgresql

revision: str = "99360f8ac979"
down_revision: str | None = "1dbee674b5e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tipos ENUM nuevos de esta fase. Se crean explícitamente una sola vez:
    # `stage_type` lo usan 3 tablas y `verification_status`/`source_type` ya
    # existen desde la Fase 2.
    stage_type = postgresql.ENUM(
        "NEW",
        "QUALIFIED",
        "CONTACT_FOUND",
        "CONTACTED",
        "OPENED",
        "REPLIED",
        "CONVERSATION",
        "INTERESTED",
        "MEETING",
        "OPPORTUNITY",
        "PROPOSAL",
        "NEGOTIATION",
        "WON",
        "LOST",
        name="stage_type",
    )
    lead_status = postgresql.ENUM(
        "OPEN", "WON", "LOST", "DISQUALIFIED", "PAUSED", name="lead_status"
    )
    reply_intent = postgresql.ENUM(
        "POSITIVE",
        "NEUTRAL",
        "NEGATIVE",
        "QUESTION",
        "PRICING",
        "MEETING_REQUEST",
        "OUT_OF_OFFICE",
        "UNSUBSCRIBE",
        "WRONG_PERSON",
        "UNKNOWN",
        name="reply_intent",
    )
    actor_type = postgresql.ENUM("USER", "SYSTEM", "AI", "PROSPECT", name="actor_type")
    activity_type = postgresql.ENUM(
        "COMPANY_FOUND",
        "EMAIL_FOUND",
        "LEAD_CREATED",
        "LEAD_QUALIFIED",
        "STAGE_CHANGED",
        "EMAIL_SENT",
        "EMAIL_DELIVERED",
        "EMAIL_OPENED",
        "EMAIL_CLICKED",
        "EMAIL_REPLIED",
        "EMAIL_BOUNCED",
        "CONVERSATION_STARTED",
        "FOLLOWUP_SCHEDULED",
        "FOLLOWUP_SENT",
        "MEETING_SCHEDULED",
        "PROPOSAL_SENT",
        "NOTE",
        "TASK_CREATED",
        "TASK_COMPLETED",
        "AI_PERSONALIZED",
        "AI_CLASSIFIED",
        "WON",
        "LOST",
        name="activity_type",
    )
    for enum_type in (stage_type, lead_status, reply_intent, actor_type, activity_type):
        enum_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "pipeline_stages",
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("stage_key", sa.String(length=60), nullable=False),
        sa.Column(
            "stage_type",
            postgresql.ENUM(
                "NEW",
                "QUALIFIED",
                "CONTACT_FOUND",
                "CONTACTED",
                "OPENED",
                "REPLIED",
                "CONVERSATION",
                "INTERESTED",
                "MEETING",
                "OPPORTUNITY",
                "PROPOSAL",
                "NEGOTIATION",
                "WON",
                "LOST",
                name="stage_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("color", sa.String(length=9), server_default="#64748b", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_won", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_lost", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "auto_advance_on",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pipeline_stages")),
        sa.UniqueConstraint("owner_id", "stage_key", name="uq_pipeline_stages_owner_key"),
    )
    op.create_index(
        op.f("ix_pipeline_stages_owner_id"), "pipeline_stages", ["owner_id"], unique=False
    )
    op.create_index(
        "ix_pipeline_stages_position", "pipeline_stages", ["owner_id", "position"], unique=False
    )
    op.create_table(
        "contacts",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("first_name", sa.String(length=120), nullable=True),
        sa.Column("last_name", sa.String(length=120), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("job_title", sa.String(length=160), nullable=True),
        sa.Column("seniority", sa.String(length=20), nullable=True),
        sa.Column("email", postgresql.CITEXT(), nullable=True),
        sa.Column(
            "email_verified",
            postgresql.ENUM(
                "UNVERIFIED",
                "SYNTAX_OK",
                "MX_OK",
                "VERIFIED",
                "RISKY",
                "INVALID",
                "BOUNCED",
                name="verification_status",
                create_type=False,
            ),
            server_default="UNVERIFIED",
            nullable=False,
        ),
        sa.Column("is_role_email", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("whatsapp", sa.String(length=20), nullable=True),
        sa.Column("linkedin_url", sa.Text(), nullable=True),
        sa.Column(
            "source",
            postgresql.ENUM(
                "GOOGLE_MAPS",
                "GOOGLE_PLACES_API",
                "APIFY",
                "WEBSITE",
                "LINKEDIN",
                "INSTAGRAM",
                "FACEBOOK",
                "MANUAL",
                "AI",
                "IMPORT",
                name="source_type",
                create_type=False,
            ),
            server_default="WEBSITE",
            nullable=False,
        ),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("do_not_contact", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
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
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_contacts_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contacts")),
    )
    op.create_index("ix_contacts_company", "contacts", ["company_id"], unique=False)
    op.create_index(
        "ix_contacts_email", "contacts", [sa.literal_column("lower(email)")], unique=False
    )
    op.create_index(op.f("ix_contacts_owner_id"), "contacts", ["owner_id"], unique=False)
    op.create_index(
        "uq_contacts_company_email",
        "contacts",
        ["company_id", sa.literal_column("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.create_table(
        "leads",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("service_id", sa.UUID(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column("stage_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "OPEN",
                "WON",
                "LOST",
                "DISQUALIFIED",
                "PAUSED",
                name="lead_status",
                create_type=False,
            ),
            server_default="OPEN",
            nullable=False,
        ),
        sa.Column("score", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("score_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("score_computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("engagement_score", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column(
            "reply_intent",
            postgresql.ENUM(
                "POSITIVE",
                "NEUTRAL",
                "NEGATIVE",
                "QUESTION",
                "PRICING",
                "MEETING_REQUEST",
                "OUT_OF_OFFICE",
                "UNSUBSCRIBE",
                "WRONG_PERSON",
                "UNKNOWN",
                name="reply_intent",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column("estimated_value", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default="COP", nullable=False),
        sa.Column("first_contact_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_contact_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_follow_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("won_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lost_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lost_reason", sa.Text(), nullable=True),
        sa.Column("sequence_id", sa.UUID(), nullable=True),
        sa.Column("sequence_step", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("sequence_paused", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_leads_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            name=op.f("fk_leads_contact_id_contacts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_leads_service_id_services"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["pipeline_stages.id"], name=op.f("fk_leads_stage_id_pipeline_stages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leads")),
        sa.UniqueConstraint("company_id", "service_id", name="uq_leads_company_service"),
    )
    op.create_index(
        "ix_leads_next_followup",
        "leads",
        ["next_follow_up_at"],
        unique=False,
        postgresql_where=sa.text("next_follow_up_at IS NOT NULL AND status = 'OPEN'"),
    )
    op.create_index(op.f("ix_leads_owner_id"), "leads", ["owner_id"], unique=False)
    op.create_index(
        "ix_leads_owner_status_activity",
        "leads",
        ["owner_id", "status", "last_activity_at"],
        unique=False,
    )
    op.create_index("ix_leads_stage_score", "leads", ["stage_id", "score"], unique=False)
    op.create_table(
        "activities",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column("lead_id", sa.UUID(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column(
            "activity_type",
            postgresql.ENUM(
                "COMPANY_FOUND",
                "EMAIL_FOUND",
                "LEAD_CREATED",
                "LEAD_QUALIFIED",
                "STAGE_CHANGED",
                "EMAIL_SENT",
                "EMAIL_DELIVERED",
                "EMAIL_OPENED",
                "EMAIL_CLICKED",
                "EMAIL_REPLIED",
                "EMAIL_BOUNCED",
                "CONVERSATION_STARTED",
                "FOLLOWUP_SCHEDULED",
                "FOLLOWUP_SENT",
                "MEETING_SCHEDULED",
                "PROPOSAL_SENT",
                "NOTE",
                "TASK_CREATED",
                "TASK_COMPLETED",
                "AI_PERSONALIZED",
                "AI_CLASSIFIED",
                "WON",
                "LOST",
                name="activity_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "actor",
            postgresql.ENUM(
                "USER", "SYSTEM", "AI", "PROSPECT", name="actor_type", create_type=False
            ),
            server_default="SYSTEM",
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_activities_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            name=op.f("fk_activities_contact_id_contacts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name=op.f("fk_activities_lead_id_leads"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activities")),
    )
    op.create_index(
        "ix_activities_company", "activities", ["company_id", "occurred_at"], unique=False
    )
    op.create_index("ix_activities_lead", "activities", ["lead_id", "occurred_at"], unique=False)
    op.create_index(
        "ix_activities_type", "activities", ["activity_type", "occurred_at"], unique=False
    )
    op.create_table(
        "lead_stage_history",
        sa.Column("lead_id", sa.UUID(), nullable=False),
        sa.Column("from_stage_id", sa.UUID(), nullable=True),
        sa.Column("to_stage_id", sa.UUID(), nullable=True),
        sa.Column(
            "from_stage_type",
            postgresql.ENUM(
                "NEW",
                "QUALIFIED",
                "CONTACT_FOUND",
                "CONTACTED",
                "OPENED",
                "REPLIED",
                "CONVERSATION",
                "INTERESTED",
                "MEETING",
                "OPPORTUNITY",
                "PROPOSAL",
                "NEGOTIATION",
                "WON",
                "LOST",
                name="stage_type",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column(
            "to_stage_type",
            postgresql.ENUM(
                "NEW",
                "QUALIFIED",
                "CONTACT_FOUND",
                "CONTACTED",
                "OPENED",
                "REPLIED",
                "CONVERSATION",
                "INTERESTED",
                "MEETING",
                "OPPORTUNITY",
                "PROPOSAL",
                "NEGOTIATION",
                "WON",
                "LOST",
                name="stage_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "actor",
            postgresql.ENUM(
                "USER", "SYSTEM", "AI", "PROSPECT", name="actor_type", create_type=False
            ),
            server_default="USER",
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "entered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
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
            ["from_stage_id"],
            ["pipeline_stages.id"],
            name=op.f("fk_lead_stage_history_from_stage_id_pipeline_stages"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
            name=op.f("fk_lead_stage_history_lead_id_leads"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_stage_id"],
            ["pipeline_stages.id"],
            name=op.f("fk_lead_stage_history_to_stage_id_pipeline_stages"),
            # SET NULL: borrar una etapa no debe borrar el historial que pasó
            # por ella; `to_stage_type` conserva la semántica.
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lead_stage_history")),
    )
    op.create_index(
        "ix_lead_stage_history_lead", "lead_stage_history", ["lead_id", "entered_at"], unique=False
    )
    op.create_index(
        "ix_lead_stage_history_type",
        "lead_stage_history",
        ["to_stage_type", "entered_at"],
        unique=False,
    )
    op.create_table(
        "tasks",
        sa.Column("lead_id", sa.UUID(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("priority", sa.SmallInteger(), server_default="2", nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_tasks_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name=op.f("fk_tasks_lead_id_leads"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
    )
    op.create_index("ix_tasks_lead", "tasks", ["lead_id"], unique=False)
    op.create_index(op.f("ix_tasks_owner_id"), "tasks", ["owner_id"], unique=False)
    op.create_index(
        "ix_tasks_pending",
        "tasks",
        ["due_at"],
        unique=False,
        postgresql_where=sa.text("completed_at IS NULL"),
    )

    # Seed de las 14 etapas del Módulo 17. Van en la migración y no en un
    # script aparte porque el CRM no arranca sin ellas: un lead necesita
    # `stage_id` NOT NULL desde el primer INSERT.
    from app.models.pipeline import DEFAULT_STAGES

    op.bulk_insert(
        sa.table(
            "pipeline_stages",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("name", sa.String),
            sa.column("stage_key", sa.String),
            sa.column("stage_type", postgresql.ENUM(name="stage_type", create_type=False)),
            sa.column("position", sa.Integer),
            sa.column("color", sa.String),
            sa.column("is_default", sa.Boolean),
            sa.column("is_won", sa.Boolean),
            sa.column("is_lost", sa.Boolean),
            sa.column("is_system", sa.Boolean),
            sa.column("auto_advance_on", postgresql.ARRAY(sa.Text)),
        ),
        [
            {
                "id": uuid.uuid4(),
                "name": stage["name"],
                "stage_key": stage["stage_key"],
                "stage_type": stage["stage_type"].value,
                "position": index,
                "color": stage.get("color", "#64748b"),
                "is_default": stage.get("is_default", False),
                "is_won": stage.get("is_won", False),
                "is_lost": stage.get("is_lost", False),
                "is_system": True,
                "auto_advance_on": stage.get("auto_advance_on", []),
            }
            for index, stage in enumerate(DEFAULT_STAGES, start=1)
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tasks_pending", table_name="tasks", postgresql_where=sa.text("completed_at IS NULL")
    )
    op.drop_index(op.f("ix_tasks_owner_id"), table_name="tasks")
    op.drop_index("ix_tasks_lead", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_lead_stage_history_type", table_name="lead_stage_history")
    op.drop_index("ix_lead_stage_history_lead", table_name="lead_stage_history")
    op.drop_table("lead_stage_history")
    op.drop_index("ix_activities_type", table_name="activities")
    op.drop_index("ix_activities_lead", table_name="activities")
    op.drop_index("ix_activities_company", table_name="activities")
    op.drop_table("activities")
    op.drop_index("ix_leads_stage_score", table_name="leads")
    op.drop_index("ix_leads_owner_status_activity", table_name="leads")
    op.drop_index(op.f("ix_leads_owner_id"), table_name="leads")
    op.drop_index(
        "ix_leads_next_followup",
        table_name="leads",
        postgresql_where=sa.text("next_follow_up_at IS NOT NULL AND status = 'OPEN'"),
    )
    op.drop_table("leads")
    op.drop_index(
        "uq_contacts_company_email",
        table_name="contacts",
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.drop_index(op.f("ix_contacts_owner_id"), table_name="contacts")
    op.drop_index("ix_contacts_email", table_name="contacts")
    op.drop_index("ix_contacts_company", table_name="contacts")
    op.drop_table("contacts")
    op.drop_index("ix_pipeline_stages_position", table_name="pipeline_stages")
    op.drop_index(op.f("ix_pipeline_stages_owner_id"), table_name="pipeline_stages")
    op.drop_table("pipeline_stages")

    # Solo los tipos introducidos por esta migración.
    for name in ("activity_type", "actor_type", "reply_intent", "lead_status", "stage_type"):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
