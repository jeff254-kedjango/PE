"""notifications (in-app per-user inbox)

Creates the general-purpose user notification inbox (misty-knitting-willow plan,
Part B). SEPARATE from notification_audit (which is append-only legal evidence with
no user_id / no read-state). This table is intentionally MUTABLE — `read_at` flips
when the user opens a notification — so it gets NO append-only trigger.

Additive only: a brand-new table on the MANAGED_TABLES allow-list; touches no existing
table. `now()` server_default is Postgres-only (same convention as the p4a/billing/
metering migrations); on SQLite (tests) the ORM create_all path supplies the default.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notifications',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=160), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('link', sa.String(length=500), nullable=True),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_notifications_user_id'), 'notifications', ['user_id'], unique=False)
    op.create_index(op.f('ix_notifications_created_at'), 'notifications', ['created_at'], unique=False)
    op.create_index('idx_notification_user_read', 'notifications', ['user_id', 'read_at'], unique=False)
    op.create_index('idx_notification_user_created', 'notifications', ['user_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_notification_user_created', table_name='notifications')
    op.drop_index('idx_notification_user_read', table_name='notifications')
    op.drop_index(op.f('ix_notifications_created_at'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_user_id'), table_name='notifications')
    op.drop_table('notifications')
