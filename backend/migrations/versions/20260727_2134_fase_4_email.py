"""fase 4 email

Revision ID: 2df69f93ac48
Revises: 99360f8ac979
Create Date: 2026-07-27 21:34:35.246610
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "2df69f93ac48"
down_revision: str | None = "99360f8ac979"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tipos ENUM nuevos de esta fase, creados explícitamente una sola vez:
    # `direction` y `channel` los usan varias tablas.
    channel = postgresql.ENUM("EMAIL", "WHATSAPP", "LINKEDIN", "PHONE", "MANUAL", name="channel")
    direction = postgresql.ENUM("OUTBOUND", "INBOUND", name="direction")
    mail_provider = postgresql.ENUM("GMAIL", "MICROSOFT", "SMTP", name="mail_provider")
    account_status = postgresql.ENUM(
        "ACTIVE", "TOKEN_EXPIRED", "REVOKED", "ERROR", "DISABLED", name="account_status"
    )
    email_status = postgresql.ENUM(
        "DRAFT",
        "QUEUED",
        "SENDING",
        "SENT",
        "DELIVERED",
        "BOUNCED",
        "FAILED",
        "CANCELLED",
        name="email_status",
    )
    email_event_type = postgresql.ENUM(
        "SENT",
        "DELIVERED",
        "OPENED",
        "CLICKED",
        "REPLIED",
        "BOUNCED",
        "COMPLAINED",
        "UNSUBSCRIBED",
        "FAILED",
        name="email_event_type",
    )
    for enum_type in (
        channel,
        direction,
        mail_provider,
        account_status,
        email_status,
        email_event_type,
    ):
        enum_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "email_accounts",
        sa.Column(
            "provider",
            postgresql.ENUM("GMAIL", "MICROSOFT", "SMTP", name="mail_provider", create_type=False),
            nullable=False,
        ),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "ACTIVE",
                "TOKEN_EXPIRED",
                "REVOKED",
                "ERROR",
                "DISABLED",
                name="account_status",
                create_type=False,
            ),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column("oauth_access_token_enc", sa.Text(), nullable=True),
        sa.Column("oauth_refresh_token_enc", sa.Text(), nullable=True),
        sa.Column("oauth_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("oauth_scopes", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("external_account_id", sa.String(length=255), nullable=True),
        sa.Column("smtp_host", sa.String(length=255), nullable=True),
        sa.Column("smtp_port", sa.Integer(), nullable=True),
        sa.Column("smtp_user", sa.String(length=255), nullable=True),
        sa.Column("smtp_password_enc", sa.Text(), nullable=True),
        sa.Column("smtp_use_tls", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("imap_host", sa.String(length=255), nullable=True),
        sa.Column("imap_port", sa.Integer(), nullable=True),
        sa.Column("imap_user", sa.String(length=255), nullable=True),
        sa.Column("imap_password_enc", sa.Text(), nullable=True),
        sa.Column("sync_cursor", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watch_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("sent_today", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sent_this_hour", sa.Integer(), server_default="0", nullable=False),
        sa.Column("counters_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_accounts")),
    )
    op.create_index(
        op.f("ix_email_accounts_owner_id"), "email_accounts", ["owner_id"], unique=False
    )
    op.create_index(
        "ix_email_accounts_unhealthy",
        "email_accounts",
        ["status"],
        unique=False,
        postgresql_where=sa.text("status <> 'ACTIVE'"),
    )
    op.create_index(
        "ix_email_accounts_watch_expiry",
        "email_accounts",
        ["watch_expires_at"],
        unique=False,
        postgresql_where=sa.text("watch_expires_at IS NOT NULL"),
    )
    op.create_index(
        "uq_email_accounts_owner_email",
        "email_accounts",
        ["owner_id", sa.literal_column("lower(email)")],
        unique=True,
    )
    op.create_table(
        "app_settings",
        sa.Column("id", sa.SmallInteger(), server_default="1", autoincrement=False, nullable=False),
        sa.Column("sender_name", sa.String(length=120), server_default="", nullable=False),
        sa.Column("default_account_id", sa.UUID(), nullable=True),
        sa.Column("reply_to", postgresql.CITEXT(), nullable=True),
        sa.Column("tracking_domain", sa.String(length=255), nullable=True),
        sa.Column("address_of_sender", sa.Text(), nullable=True),
        sa.Column("country_code", sa.String(length=2), server_default="CO", nullable=False),
        sa.Column("phone_region", sa.String(length=2), server_default="CO", nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="COP", nullable=False),
        sa.Column("locale", sa.String(length=10), server_default="es-CO", nullable=False),
        sa.Column(
            "timezone", sa.String(length=60), server_default="America/Bogota", nullable=False
        ),
        sa.Column("daily_send_limit", sa.Integer(), server_default="100", nullable=False),
        sa.Column("hourly_send_limit", sa.Integer(), server_default="20", nullable=False),
        sa.Column("min_seconds_between", sa.Integer(), server_default="45", nullable=False),
        sa.Column("send_window_start", sa.Time(), server_default="08:00", nullable=False),
        sa.Column("send_window_end", sa.Time(), server_default="18:00", nullable=False),
        sa.Column("skip_weekends", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("warmup_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("warmup_started_on", sa.Date(), nullable=True),
        sa.Column("ai_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("ai_model", sa.String(length=60), server_default="claude-opus-5", nullable=False),
        sa.Column("ai_tone", sa.String(length=20), server_default="usted", nullable=False),
        sa.Column(
            "discovery_provider",
            sa.String(length=40),
            server_default="google_maps_scraper",
            nullable=False,
        ),
        sa.Column("fallback_discovery_provider", sa.String(length=40), nullable=True),
        sa.Column("scraper_concurrency", sa.SmallInteger(), server_default="2", nullable=False),
        sa.Column("scraper_delay_ms_min", sa.Integer(), server_default="1200", nullable=False),
        sa.Column("scraper_delay_ms_max", sa.Integer(), server_default="3500", nullable=False),
        sa.Column("scraper_headless", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("google_places_key_enc", sa.Text(), nullable=True),
        sa.Column("apify_token_enc", sa.Text(), nullable=True),
        sa.Column(
            "score_weights",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default='{"fit": 0.3, "opportunity": 0.25, "contactability": 0.2, "data_quality": 0.1, "intent": 0.1, "timing": 0.05}',
            nullable=False,
        ),
        sa.Column(
            "automations_paused", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
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
        sa.CheckConstraint("id = 1", name=op.f("ck_app_settings_single_row")),
        sa.ForeignKeyConstraint(
            ["default_account_id"],
            ["email_accounts.id"],
            name=op.f("fk_app_settings_default_account_id_email_accounts"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_settings")),
    )
    op.create_table(
        "email_templates",
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("service_id", sa.UUID(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column(
            "variables_used",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("times_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("open_rate", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("reply_rate", sa.Numeric(precision=5, scale=2), nullable=True),
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
            name=op.f("fk_email_templates_service_id_services"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_templates")),
    )
    op.create_index("ix_email_templates_category", "email_templates", ["category"], unique=False)
    op.create_index(
        op.f("ix_email_templates_owner_id"), "email_templates", ["owner_id"], unique=False
    )
    op.create_index(
        "uq_email_templates_owner_name",
        "email_templates",
        ["owner_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "conversations",
        sa.Column("lead_id", sa.UUID(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column(
            "channel",
            postgresql.ENUM(
                "EMAIL",
                "WHATSAPP",
                "LINKEDIN",
                "PHONE",
                "MANUAL",
                name="channel",
                create_type=False,
            ),
            server_default="EMAIL",
            nullable=False,
        ),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("thread_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="OPEN", nullable=False),
        sa.Column("is_unread", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("message_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_direction",
            postgresql.ENUM("OUTBOUND", "INBOUND", name="direction", create_type=False),
            nullable=True,
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
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            name=op.f("fk_conversations_contact_id_contacts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
            name=op.f("fk_conversations_lead_id_leads"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        sa.UniqueConstraint(
            "lead_id", "channel", "thread_key", name="uq_conversations_lead_channel_thread"
        ),
    )
    op.create_index(op.f("ix_conversations_owner_id"), "conversations", ["owner_id"], unique=False)
    op.create_index(
        "ix_conversations_status",
        "conversations",
        ["owner_id", "status", "last_message_at"],
        unique=False,
    )
    op.create_index(
        "ix_conversations_unread",
        "conversations",
        ["owner_id", "channel"],
        unique=False,
        postgresql_where=sa.text("is_unread"),
    )
    op.create_table(
        "conversation_messages",
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column(
            "channel",
            postgresql.ENUM(
                "EMAIL",
                "WHATSAPP",
                "LINKEDIN",
                "PHONE",
                "MANUAL",
                name="channel",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "direction",
            postgresql.ENUM("OUTBOUND", "INBOUND", name="direction", create_type=False),
            nullable=False,
        ),
        sa.Column("author_name", sa.String(length=160), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("snippet", sa.String(length=255), nullable=True),
        sa.Column("attachments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_automated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_conversation_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_messages")),
    )
    op.create_index(
        op.f("ix_conversation_messages_owner_id"),
        "conversation_messages",
        ["owner_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_messages_thread",
        "conversation_messages",
        ["conversation_id", "occurred_at"],
        unique=False,
    )
    op.create_table(
        "email_messages",
        sa.Column("conversation_message_id", sa.UUID(), nullable=True),
        sa.Column("conversation_id", sa.UUID(), nullable=True),
        sa.Column("lead_id", sa.UUID(), nullable=True),
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column("template_id", sa.UUID(), nullable=True),
        sa.Column("email_account_id", sa.UUID(), nullable=True),
        sa.Column(
            "direction",
            postgresql.ENUM("OUTBOUND", "INBOUND", name="direction", create_type=False),
            nullable=False,
        ),
        sa.Column("from_email", postgresql.CITEXT(), nullable=False),
        sa.Column("to_email", postgresql.CITEXT(), nullable=False),
        sa.Column("cc", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT",
                "QUEUED",
                "SENDING",
                "SENT",
                "DELIVERED",
                "BOUNCED",
                "FAILED",
                "CANCELLED",
                name="email_status",
                create_type=False,
            ),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column(
            "provider",
            postgresql.ENUM("GMAIL", "MICROSOFT", "SMTP", name="mail_provider", create_type=False),
            nullable=True,
        ),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("provider_thread_id", sa.String(length=255), nullable=True),
        sa.Column("in_reply_to", sa.String(length=255), nullable=True),
        sa.Column("references_header", sa.Text(), nullable=True),
        sa.Column(
            "tracking_token", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column(
            "unsubscribe_token",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tracking_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bounced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("open_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("click_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("bounce_type", sa.String(length=20), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("is_ai_generated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("ai_model", sa.String(length=60), nullable=True),
        sa.Column(
            "was_edited_by_user", sa.Boolean(), server_default=sa.text("false"), nullable=False
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
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            name=op.f("fk_email_messages_contact_id_contacts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_email_messages_conversation_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_message_id"],
            ["conversation_messages.id"],
            name=op.f("fk_email_messages_conversation_message_id_conversation_messages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["email_account_id"],
            ["email_accounts.id"],
            name=op.f("fk_email_messages_email_account_id_email_accounts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
            name=op.f("fk_email_messages_lead_id_leads"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["email_templates.id"],
            name=op.f("fk_email_messages_template_id_email_templates"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_messages")),
        sa.UniqueConstraint(
            "conversation_message_id", name=op.f("uq_email_messages_conversation_message_id")
        ),
    )
    op.create_index(
        "ix_email_messages_account", "email_messages", ["email_account_id", "sent_at"], unique=False
    )
    op.create_index(
        "ix_email_messages_conversation",
        "email_messages",
        ["conversation_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_email_messages_lead", "email_messages", ["lead_id", "created_at"], unique=False
    )
    op.create_index(
        op.f("ix_email_messages_owner_id"), "email_messages", ["owner_id"], unique=False
    )
    op.create_index(
        "ix_email_messages_pending",
        "email_messages",
        ["status"],
        unique=False,
        postgresql_where=sa.text("status IN ('QUEUED', 'SENDING')"),
    )
    op.create_index(
        "ix_email_messages_thread",
        "email_messages",
        ["provider_thread_id"],
        unique=False,
        postgresql_where=sa.text("provider_thread_id IS NOT NULL"),
    )
    op.create_index(
        "uq_email_messages_provider_message_id",
        "email_messages",
        ["provider_message_id"],
        unique=True,
        postgresql_where=sa.text("provider_message_id IS NOT NULL"),
    )
    op.create_index(
        "uq_email_messages_tracking_token", "email_messages", ["tracking_token"], unique=True
    )
    op.create_index(
        "uq_email_messages_unsubscribe_token", "email_messages", ["unsubscribe_token"], unique=True
    )
    op.create_table(
        "email_links",
        sa.Column("email_message_id", sa.UUID(), nullable=False),
        sa.Column(
            "tracking_token", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("click_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("first_clicked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_clicked_at", sa.DateTime(timezone=True), nullable=True),
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
            ["email_message_id"],
            ["email_messages.id"],
            name=op.f("fk_email_links_email_message_id_email_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_links")),
    )
    op.create_index("ix_email_links_message", "email_links", ["email_message_id"], unique=False)
    op.create_index("uq_email_links_token", "email_links", ["tracking_token"], unique=True)
    op.create_table(
        "suppression_list",
        sa.Column("email", postgresql.CITEXT(), nullable=True),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("source_email_id", sa.UUID(), nullable=True),
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
            ["source_email_id"],
            ["email_messages.id"],
            name=op.f("fk_suppression_list_source_email_id_email_messages"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppression_list")),
    )
    op.create_index(
        op.f("ix_suppression_list_owner_id"), "suppression_list", ["owner_id"], unique=False
    )
    op.create_index(
        "uq_suppression_domain",
        "suppression_list",
        ["owner_id", sa.literal_column("lower(domain)")],
        unique=True,
        postgresql_where=sa.text("domain IS NOT NULL"),
    )
    op.create_index(
        "uq_suppression_email",
        "suppression_list",
        ["owner_id", sa.literal_column("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.create_table(
        "email_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("email_message_id", sa.UUID(), nullable=False),
        sa.Column("email_link_id", sa.UUID(), nullable=True),
        sa.Column(
            "event_type",
            postgresql.ENUM(
                "SENT",
                "DELIVERED",
                "OPENED",
                "CLICKED",
                "REPLIED",
                "BOUNCED",
                "COMPLAINED",
                "UNSUBSCRIBED",
                "FAILED",
                name="email_event_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("is_likely_bot", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["email_link_id"],
            ["email_links.id"],
            name=op.f("fk_email_events_email_link_id_email_links"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["email_message_id"],
            ["email_messages.id"],
            name=op.f("fk_email_events_email_message_id_email_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_events")),
    )
    op.create_index(
        "ix_email_events_message", "email_events", ["email_message_id", "occurred_at"], unique=False
    )
    op.create_index(
        "ix_email_events_type", "email_events", ["event_type", "occurred_at"], unique=False
    )

    # Fila única de configuración. Se siembra en la migración porque el CRM la
    # lee en cada envío: sin ella no hay límites, ni identidad de remitente,
    # ni interruptor de automatizaciones.
    op.execute(
        sa.text(
            "INSERT INTO app_settings (id, sender_name) VALUES (1, '') ON CONFLICT (id) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_email_events_type", table_name="email_events")
    op.drop_index("ix_email_events_message", table_name="email_events")
    op.drop_table("email_events")
    op.drop_index(
        "uq_suppression_email",
        table_name="suppression_list",
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.drop_index(
        "uq_suppression_domain",
        table_name="suppression_list",
        postgresql_where=sa.text("domain IS NOT NULL"),
    )
    op.drop_index(op.f("ix_suppression_list_owner_id"), table_name="suppression_list")
    op.drop_table("suppression_list")
    op.drop_index("uq_email_links_token", table_name="email_links")
    op.drop_index("ix_email_links_message", table_name="email_links")
    op.drop_table("email_links")
    op.drop_index("uq_email_messages_unsubscribe_token", table_name="email_messages")
    op.drop_index("uq_email_messages_tracking_token", table_name="email_messages")
    op.drop_index(
        "uq_email_messages_provider_message_id",
        table_name="email_messages",
        postgresql_where=sa.text("provider_message_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_email_messages_thread",
        table_name="email_messages",
        postgresql_where=sa.text("provider_thread_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_email_messages_pending",
        table_name="email_messages",
        postgresql_where=sa.text("status IN ('QUEUED', 'SENDING')"),
    )
    op.drop_index(op.f("ix_email_messages_owner_id"), table_name="email_messages")
    op.drop_index("ix_email_messages_lead", table_name="email_messages")
    op.drop_index("ix_email_messages_conversation", table_name="email_messages")
    op.drop_index("ix_email_messages_account", table_name="email_messages")
    op.drop_table("email_messages")
    op.drop_index("ix_conversation_messages_thread", table_name="conversation_messages")
    op.drop_index(op.f("ix_conversation_messages_owner_id"), table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_index(
        "ix_conversations_unread", table_name="conversations", postgresql_where=sa.text("is_unread")
    )
    op.drop_index("ix_conversations_status", table_name="conversations")
    op.drop_index(op.f("ix_conversations_owner_id"), table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("uq_email_templates_owner_name", table_name="email_templates")
    op.drop_index(op.f("ix_email_templates_owner_id"), table_name="email_templates")
    op.drop_index("ix_email_templates_category", table_name="email_templates")
    op.drop_table("email_templates")
    op.drop_table("app_settings")
    op.drop_index("uq_email_accounts_owner_email", table_name="email_accounts")
    op.drop_index(
        "ix_email_accounts_watch_expiry",
        table_name="email_accounts",
        postgresql_where=sa.text("watch_expires_at IS NOT NULL"),
    )
    op.drop_index(
        "ix_email_accounts_unhealthy",
        table_name="email_accounts",
        postgresql_where=sa.text("status <> 'ACTIVE'"),
    )
    op.drop_index(op.f("ix_email_accounts_owner_id"), table_name="email_accounts")
    op.drop_table("email_accounts")

    for name in (
        "email_event_type",
        "email_status",
        "account_status",
        "mail_provider",
        "direction",
        "channel",
    ):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
