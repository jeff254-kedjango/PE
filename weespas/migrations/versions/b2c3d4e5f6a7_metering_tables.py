"""metering_tables (metering_event + user_usage_profile)

Adds the two §8 metering / company-detection tables (PE/billing_architecture.md §8).
Additive only — the include_object allow-list in env.py restricts autogenerate to
MANAGED_TABLES, and these CREATEs touch no existing table. Neither table is
append-only enforced at the DB level: metering_event is a best-effort behavioural log
(not money / not legal evidence), and user_usage_profile is intentionally MUTABLE (the
beat job upserts each user's latest score). The integrity-critical tables
(payment_ledger, notification_audit) keep their triggers; these do not need them.

`now()` server_default is Postgres-only (same convention as the p4a + billing
migrations); on SQLite (tests) the ORM/`create_all` path supplies defaults instead.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'metering_event',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=True),
        sa.Column('session_id', sa.String(), nullable=True),
        sa.Column('action', sa.String(length=32), nullable=False),
        sa.Column('target_ref', sa.String(length=64), nullable=True),
        sa.Column('aoi_code', sa.String(length=64), nullable=True),
        sa.Column('meta', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['session_id'], ['user_sessions.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_metering_event_user_id'), 'metering_event', ['user_id'], unique=False)
    op.create_index(op.f('ix_metering_event_session_id'), 'metering_event', ['session_id'], unique=False)
    op.create_index(op.f('ix_metering_event_action'), 'metering_event', ['action'], unique=False)
    op.create_index(op.f('ix_metering_event_created_at'), 'metering_event', ['created_at'], unique=False)
    op.create_index('idx_metering_user_action_time', 'metering_event',
                    ['user_id', 'action', 'created_at'], unique=False)

    op.create_table(
        'user_usage_profile',
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('score', sa.Float(), nullable=False),
        sa.Column('is_metered', sa.SmallInteger(), nullable=False),
        sa.Column('volume', sa.Integer(), nullable=False),
        sa.Column('breadth', sa.Integer(), nullable=False),
        sa.Column('export_count', sa.Integer(), nullable=False),
        sa.Column('automation', sa.Float(), nullable=False),
        sa.Column('corporate_domain', sa.SmallInteger(), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id'),
    )
    op.create_index('idx_usage_profile_metered', 'user_usage_profile', ['is_metered'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_usage_profile_metered', table_name='user_usage_profile')
    op.drop_table('user_usage_profile')
    op.drop_index('idx_metering_user_action_time', table_name='metering_event')
    op.drop_index(op.f('ix_metering_event_created_at'), table_name='metering_event')
    op.drop_index(op.f('ix_metering_event_action'), table_name='metering_event')
    op.drop_index(op.f('ix_metering_event_session_id'), table_name='metering_event')
    op.drop_index(op.f('ix_metering_event_user_id'), table_name='metering_event')
    op.drop_table('metering_event')
