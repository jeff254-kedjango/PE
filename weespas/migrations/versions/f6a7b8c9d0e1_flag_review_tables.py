"""flag_review + flag_review_view (staff/admin review queue for structural flags)

Completes the "flag a building → staff/admin review" loop. When a certifier records a
structural flag, one `flag_review` row is opened (in the flag's own transaction) as the
shared, group-addressed alert; the badge nags every staff/admin until ANY one marks it
seen, recording who acknowledged it. `flag_review_view` counts DISTINCT viewers
(UNIQUE(review_id, user_id)).

SEPARATE from both `notifications` (per-user inbox) and `notification_audit` (append-only
legal spine): this is a group-shared, first-wins-acknowledged record. It is MUTABLE
(seen_at flips once) so it gets NO append-only trigger.

Additive only: two brand-new tables on the MANAGED_TABLES allow-list; touches no existing
table. `now()` server_default is Postgres-only (same convention as the prior migrations);
on SQLite (tests) the ORM create_all path supplies the default.

Revision ID: f6a7b8c9d0e1
Revises: d4e5f6a7b8c9
Create Date: 2026-06-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'flag_review',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('flag_id', sa.String(), nullable=False),
        sa.Column('seen_by_id', sa.String(), nullable=True),
        sa.Column('seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['flag_id'], ['structural_flag.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['seen_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        # One review per flag — makes create_for_flag idempotent at the DB.
        sa.UniqueConstraint('flag_id', name='uq_flag_review_flag'),
    )
    # flag_id already has a UNIQUE index from the constraint above; seen_by_id is the
    # FK we look acknowledgers up by.
    op.create_index(op.f('ix_flag_review_seen_by_id'), 'flag_review', ['seen_by_id'], unique=False)
    # Open-count badge: WHERE seen_at IS NULL.
    op.create_index('idx_flag_review_seen_at', 'flag_review', ['seen_at'], unique=False)
    # Newest-first queue list: ORDER BY created_at DESC.
    op.create_index('idx_flag_review_created', 'flag_review', ['created_at'], unique=False)

    op.create_table(
        'flag_review_view',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('review_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('viewed_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['review_id'], ['flag_review.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        # One view row per person per review — "views" = distinct people.
        sa.UniqueConstraint('review_id', 'user_id', name='uq_flag_review_view_review_user'),
    )
    # No standalone review_id index: the UNIQUE(review_id, user_id) above already
    # serves WHERE review_id=? from its leftmost column.
    op.create_index(op.f('ix_flag_review_view_user_id'), 'flag_review_view', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_flag_review_view_user_id'), table_name='flag_review_view')
    op.drop_table('flag_review_view')
    op.drop_index('idx_flag_review_created', table_name='flag_review')
    op.drop_index('idx_flag_review_seen_at', table_name='flag_review')
    op.drop_index(op.f('ix_flag_review_seen_by_id'), table_name='flag_review')
    op.drop_table('flag_review')
