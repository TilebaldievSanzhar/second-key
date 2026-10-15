"""Быстрые причины в ответе снабжения (answer_reasons)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-15 10:54:54.118695+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # у существующих вопросов причин нет — пустой список
    op.add_column(
        "findings",
        sa.Column(
            "answer_reasons",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("findings", "answer_reasons")
