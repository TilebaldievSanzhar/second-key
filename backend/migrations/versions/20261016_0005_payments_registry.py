"""Оплаты поставщикам, сведения реестра, оплаты и реестр в досье поставщика

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-16 18:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("run_suppliers", sa.Column("payment", JSONB, nullable=True))
    op.add_column("run_suppliers", sa.Column("registry", JSONB, nullable=True))
    op.create_table(
        "payments",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("payment_key", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("supplier", sa.String(length=300), nullable=False),
        sa.Column("supplier_inn", sa.String(length=32), nullable=True),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("doc_no", sa.String(length=300), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_payments_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["audit_runs.id"], name=op.f("fk_payments_run_id_audit_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
    )
    op.create_index(op.f("ix_payments_org_id"), "payments", ["org_id"], unique=False)
    op.create_index("ix_payments_run_supplier", "payments", ["run_id", "supplier"], unique=False)
    op.create_table(
        "registry_records",
        sa.Column("inn", sa.String(length=32), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("data", JSONB, nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_registry_records_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_registry_records_created_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_registry_records")),
        sa.UniqueConstraint(
            "inn", "source", "org_id", name="uq_registry_records_inn", postgresql_nulls_not_distinct=True
        ),
    )
    op.create_index(op.f("ix_registry_records_inn"), "registry_records", ["inn"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_registry_records_inn"), table_name="registry_records")
    op.drop_table("registry_records")
    op.drop_index("ix_payments_run_supplier", table_name="payments")
    op.drop_index(op.f("ix_payments_org_id"), table_name="payments")
    op.drop_table("payments")
    op.drop_column("run_suppliers", "registry")
    op.drop_column("run_suppliers", "payment")
