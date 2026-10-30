"""add gift codes (codes, redemptions and their settings)

Revision ID: c3a8f1d27b54
Revises: 9b4d2e7a1c63
Create Date: 2026-10-30 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3a8f1d27b54"
down_revision: str | None = "9b4d2e7a1c63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("gift_redemptions", "gift_codes", "gift_settings")


def _create_gift_codes() -> None:
    op.create_table(
        "gift_codes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("times_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.BigInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )


def _create_gift_redemptions() -> None:
    op.create_table(
        "gift_redemptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("gift_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gift_id", "user_id", name="uq_gift_redemption_user"),
    )
    op.create_index("ix_gift_redemptions_gift_id", "gift_redemptions", ["gift_id"])


def _create_gift_settings() -> None:
    op.create_table(
        "gift_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    builders = {
        "gift_codes": _create_gift_codes,
        "gift_redemptions": _create_gift_redemptions,
        "gift_settings": _create_gift_settings,
    }
    for table, build in builders.items():
        if table not in existing:
            build()


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table in TABLES:
        if table in existing:
            op.drop_table(table)
