"""unicidad real con owner_id nulo (NULLS NOT DISTINCT)

Postgres trata cada NULL como distinto de cualquier otro, así que un índice
único sobre `(owner_id, algo)` no impide nada mientras `owner_id` sea NULL —
que es exactamente el caso del MVP de un solo usuario. Las restricciones
existían pero no restringían: se podían crear dos servicios con el mismo
nombre, dos plantillas iguales, dos empresas con el mismo `dedupe_key` o dos
entradas de supresión repetidas.

`NULLS NOT DISTINCT` (Postgres 15+) hace que los NULL se comparen entre sí, con
lo que la unicidad vuelve a valer hoy y seguirá valiendo cuando `owner_id`
tenga valor.

Revision ID: 8c1a4f6d2b73
Revises: 2df69f93ac48
Create Date: 2026-07-27 22:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "8c1a4f6d2b73"
down_revision: str | None = "2df69f93ac48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (nombre, tabla, definición de columnas, cláusula WHERE opcional)
_INDEXES: tuple[tuple[str, str, str, str | None], ...] = (
    ("uq_services_owner_name", "services", "owner_id, lower(name)", None),
    ("uq_email_templates_owner_name", "email_templates", "owner_id, lower(name)", None),
    ("uq_email_accounts_owner_email", "email_accounts", "owner_id, lower(email)", None),
    (
        "uq_suppression_email",
        "suppression_list",
        "owner_id, lower(email)",
        "email IS NOT NULL",
    ),
    (
        "uq_suppression_domain",
        "suppression_list",
        "owner_id, lower(domain)",
        "domain IS NOT NULL",
    ),
)

# (nombre, tabla, columnas)
_CONSTRAINTS: tuple[tuple[str, str, str], ...] = (
    ("uq_companies_owner_dedupe_key", "companies", "owner_id, dedupe_key"),
    ("uq_pipeline_stages_owner_key", "pipeline_stages", "owner_id, stage_key"),
)


def _recreate_indexes(*, nulls_not_distinct: bool) -> None:
    clause = " NULLS NOT DISTINCT" if nulls_not_distinct else ""
    for name, table, columns, where in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
        where_sql = f" WHERE {where}" if where else ""
        op.execute(f"CREATE UNIQUE INDEX {name} ON {table} ({columns}){clause}{where_sql}")


def _recreate_constraints(*, nulls_not_distinct: bool) -> None:
    clause = " NULLS NOT DISTINCT" if nulls_not_distinct else ""
    for name, table, columns in _CONSTRAINTS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE{clause} ({columns})")


def upgrade() -> None:
    _recreate_indexes(nulls_not_distinct=True)
    _recreate_constraints(nulls_not_distinct=True)


def downgrade() -> None:
    _recreate_indexes(nulls_not_distinct=False)
    _recreate_constraints(nulls_not_distinct=False)
