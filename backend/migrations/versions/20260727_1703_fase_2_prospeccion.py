"""fase 2 prospeccion

Revision ID: 1dbee674b5e4
Revises: 46e017724c24
Create Date: 2026-07-27 17:03:40.587523
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1dbee674b5e4"
down_revision: str | None = "46e017724c24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Los tipos ENUM se crean una sola vez y explícitamente. Dejar que lo haga
    # create_table implícitamente falla: `source_type` lo usan 5 tablas y
    # `job_status` ya existe desde la migración de la Fase 1.
    source_type = postgresql.ENUM(
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
    )
    verification_status = postgresql.ENUM(
        "UNVERIFIED",
        "SYNTAX_OK",
        "MX_OK",
        "VERIFIED",
        "RISKY",
        "INVALID",
        "BOUNCED",
        name="verification_status",
    )
    source_type.create(op.get_bind(), checkfirst=True)
    verification_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "companies",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=160), nullable=True),
        sa.Column(
            "categories",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("state", sa.String(length=120), nullable=True),
        sa.Column("country", sa.String(length=120), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("phone_raw", sa.String(length=60), nullable=True),
        sa.Column("email", postgresql.CITEXT(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("website_domain", sa.String(length=255), nullable=True),
        sa.Column("google_maps_url", sa.Text(), nullable=True),
        sa.Column("google_ftid", sa.String(length=120), nullable=True),
        sa.Column("google_place_id", sa.String(length=255), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("rating", sa.Numeric(precision=2, scale=1), nullable=True),
        sa.Column("reviews_count", sa.Integer(), nullable=True),
        sa.Column("price_level", sa.SmallInteger(), nullable=True),
        sa.Column("opening_hours", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "is_permanently_closed", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("employee_range", sa.String(length=40), nullable=True),
        sa.Column("data_quality_score", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("dedupe_key", sa.String(length=40), nullable=False),
        sa.Column("first_extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_enriched_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
        sa.UniqueConstraint("owner_id", "dedupe_key", name="uq_companies_owner_dedupe_key"),
    )
    op.create_index("ix_companies_city_category", "companies", ["city", "category"], unique=False)
    op.create_index(
        "ix_companies_name_trgm",
        "companies",
        [sa.literal_column("name gin_trgm_ops")],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(op.f("ix_companies_owner_id"), "companies", ["owner_id"], unique=False)
    op.create_index(
        "ix_companies_website_domain",
        "companies",
        ["website_domain"],
        unique=False,
        postgresql_where=sa.text("website_domain IS NOT NULL"),
    )
    op.create_index(
        "uq_companies_google_ftid",
        "companies",
        ["google_ftid"],
        unique=True,
        postgresql_where=sa.text("google_ftid IS NOT NULL"),
    )
    op.create_index(
        "uq_companies_google_place_id",
        "companies",
        ["google_place_id"],
        unique=True,
        postgresql_where=sa.text("google_place_id IS NOT NULL"),
    )
    op.create_table(
        "services",
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("ideal_customer", sa.String(length=255), nullable=True),
        sa.Column(
            "target_industries",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "problems_solved",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "opportunity_signals",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("value_proposition", sa.Text(), nullable=True),
        sa.Column("price_from", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default="COP", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_services")),
    )
    op.create_index(op.f("ix_services_owner_id"), "services", ["owner_id"], unique=False)
    op.create_index(
        "uq_services_owner_name",
        "services",
        ["owner_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "company_signals",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("signal_key", sa.String(length=60), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_company_signals_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_signals")),
        sa.UniqueConstraint("company_id", "signal_key", name="uq_company_signals_company_key"),
    )
    op.create_index("ix_company_signals_key", "company_signals", ["signal_key"], unique=False)
    op.create_table(
        "company_socials",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("handle", sa.String(length=120), nullable=True),
        sa.Column("followers_count", sa.Integer(), nullable=True),
        sa.Column("last_post_at", sa.DateTime(timezone=True), nullable=True),
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
            nullable=False,
        ),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
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
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_company_socials_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_socials")),
        sa.UniqueConstraint("company_id", "platform", "url", name="uq_company_socials_company_url"),
    )
    op.create_table(
        "company_sources",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("field_name", sa.String(length=60), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
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
            nullable=False,
        ),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("confidence", sa.SmallInteger(), server_default="50", nullable=False),
        sa.Column(
            "verification",
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
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
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
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_company_sources_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_sources")),
        sa.UniqueConstraint(
            "company_id", "field_name", "value", name="uq_company_sources_company_field_value"
        ),
    )
    op.create_index(
        "ix_company_sources_company_field",
        "company_sources",
        ["company_id", "field_name"],
        unique=False,
    )
    op.create_table(
        "searches",
        sa.Column("service_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("business_type", sa.String(length=160), nullable=False),
        sa.Column(
            "keywords",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("country", sa.String(length=120), nullable=True),
        sa.Column("region", sa.String(length=120), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("zone", sa.String(length=160), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column(
            "radius_km", sa.Numeric(precision=6, scale=2), server_default="10", nullable=False
        ),
        sa.Column("target_count", sa.Integer(), server_default="100", nullable=False),
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
            server_default="GOOGLE_MAPS",
            nullable=False,
        ),
        sa.Column("min_rating", sa.Numeric(precision=2, scale=1), nullable=True),
        sa.Column("max_reviews", sa.Integer(), nullable=True),
        sa.Column("exclude_chains", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("auto_enrich", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("auto_score", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
            name=op.f("fk_searches_service_id_services"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_searches")),
    )
    op.create_index(op.f("ix_searches_owner_id"), "searches", ["owner_id"], unique=False)
    op.create_table(
        "search_runs",
        sa.Column("search_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "QUEUED",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                name="job_status",
                create_type=False,
            ),
            server_default="QUEUED",
            nullable=False,
        ),
        sa.Column(
            "provider",
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
            nullable=False,
        ),
        sa.Column("results_found", sa.Integer(), server_default="0", nullable=False),
        sa.Column("results_new", sa.Integer(), server_default="0", nullable=False),
        sa.Column("results_duplicate", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            ["job_id"], ["jobs.id"], name=op.f("fk_search_runs_job_id_jobs"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["search_id"],
            ["searches.id"],
            name=op.f("fk_search_runs_search_id_searches"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_runs")),
    )
    op.create_index(
        "ix_search_runs_search_created", "search_runs", ["search_id", "created_at"], unique=False
    )
    op.create_table(
        "search_results",
        sa.Column("search_run_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("is_new", sa.Boolean(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_search_results_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["search_run_id"],
            ["search_runs.id"],
            name=op.f("fk_search_results_search_run_id_search_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("search_run_id", "company_id", name=op.f("pk_search_results")),
    )


def downgrade() -> None:
    op.drop_table("search_results")
    op.drop_index("ix_search_runs_search_created", table_name="search_runs")
    op.drop_table("search_runs")
    op.drop_index(op.f("ix_searches_owner_id"), table_name="searches")
    op.drop_table("searches")
    op.drop_index("ix_company_sources_company_field", table_name="company_sources")
    op.drop_table("company_sources")
    op.drop_table("company_socials")
    op.drop_index("ix_company_signals_key", table_name="company_signals")
    op.drop_table("company_signals")
    op.drop_index("uq_services_owner_name", table_name="services")
    op.drop_index(op.f("ix_services_owner_id"), table_name="services")
    op.drop_table("services")
    op.drop_index(
        "uq_companies_google_place_id",
        table_name="companies",
        postgresql_where=sa.text("google_place_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_companies_google_ftid",
        table_name="companies",
        postgresql_where=sa.text("google_ftid IS NOT NULL"),
    )
    op.drop_index(
        "ix_companies_website_domain",
        table_name="companies",
        postgresql_where=sa.text("website_domain IS NOT NULL"),
    )
    op.drop_index(op.f("ix_companies_owner_id"), table_name="companies")
    op.drop_index("ix_companies_name_trgm", table_name="companies", postgresql_using="gin")
    op.drop_index("ix_companies_city_category", table_name="companies")
    op.drop_table("companies")

    # Solo los tipos introducidos por esta migración. `job_status` pertenece a
    # la Fase 1 y lo borra su propio downgrade.
    sa.Enum(name="verification_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="source_type").drop(op.get_bind(), checkfirst=True)
