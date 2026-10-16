"""Связки снабженца и поставщика: входящие номера строк и досье поставщиков прогона

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-16 09:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("purchase_lines", sa.Column("doc_in_no", sa.String(length=64), nullable=True))
    op.add_column("purchase_lines", sa.Column("doc_in_date", sa.Date(), nullable=True))
    op.create_table(
        "run_suppliers",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("inn", sa.String(length=32), nullable=True),
        sa.Column("inn_kind", sa.String(length=8), nullable=True),
        sa.Column("inn_date", sa.Date(), nullable=True),
        sa.Column("spend", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("lines", sa.Integer(), nullable=False),
        sa.Column("share", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("first_date", sa.Date(), nullable=False),
        sa.Column("last_date", sa.Date(), nullable=False),
        sa.Column("skus", sa.Integer(), nullable=False),
        sa.Column("buyers", JSONB, nullable=False),
        sa.Column("card", JSONB, nullable=True),
        sa.Column("traces", JSONB, nullable=False),
        sa.Column("linked", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_run_suppliers_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["audit_runs.id"], name=op.f("fk_run_suppliers_run_id_audit_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_suppliers")),
    )
    op.create_index(op.f("ix_run_suppliers_org_id"), "run_suppliers", ["org_id"], unique=False)
    op.create_index("ix_run_suppliers_run_spend", "run_suppliers", ["run_id", "spend"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_run_suppliers_run_spend", table_name="run_suppliers")
    op.drop_index(op.f("ix_run_suppliers_org_id"), table_name="run_suppliers")
    op.drop_table("run_suppliers")
    op.drop_column("purchase_lines", "doc_in_date")
    op.drop_column("purchase_lines", "doc_in_no")
