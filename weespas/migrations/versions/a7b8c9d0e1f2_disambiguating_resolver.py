"""disambiguating resolver: building_link cols + building_link_candidate table

Supports the attribute-aware "bad pin" resolver (the immutable-napping-orbit plan):

  - building_link gains:
      * confirmed_by_agent : a human (listing owner/agent) confirmed the building. Such a
                             link is AUTHORITATIVE — the auto-resolver/backfill never
                             overwrite it. server_default false so every existing row is
                             honestly "auto-resolved, not human-confirmed".
      * candidate_count    : how many plausible candidates the resolver saw (audit only).
  - building_link_candidate (NEW table) : the top-N scored footprints a clustered pin could
      be, frozen at resolve time, so the confirm-UI can offer them and an ambiguous
      listing's worst-case provisional tier can be read from exactly these buildings.

Both `building_link` and `building_link_candidate` are in the Alembic allow-list
(env.py MANAGED_TABLES). `building_link` is an additive ALTER (two new columns, no DROP);
the candidate table is a plain CREATE. Downgrade reverses exactly what this adds.

`server_default` for the booleans is the cross-dialect `false` literal (works on PG; on
SQLite the ORM `create_all` path supplies the default in tests). New `match_method` values
('disambiguated', 'agent_confirmed', 'land_aggregate') fit the existing String(16) — no
type change.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- additive columns on building_link ---
    op.add_column(
        'building_link',
        sa.Column('confirmed_by_agent', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
    )
    op.add_column(
        'building_link',
        sa.Column('candidate_count', sa.SmallInteger(), nullable=True),
    )

    # --- new candidate store ---
    op.create_table(
        'building_link_candidate',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('listing_id', sa.String(), nullable=False),
        sa.Column('aoi_code', sa.String(length=64), nullable=False),
        sa.Column('insar_building_id', sa.BigInteger(), nullable=False),
        sa.Column('rank', sa.SmallInteger(), nullable=False),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('distance_m', sa.Float(), nullable=True),
        sa.Column('height_m', sa.Float(), nullable=True),
        sa.Column('n_floors', sa.SmallInteger(), nullable=True),
        sa.Column('danger_level_at_resolve', sa.SmallInteger(), nullable=True),
        sa.Column('vetoed', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['listing_id'], ['properties.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('listing_id', 'insar_building_id',
                            name='uq_building_link_candidate_listing_building'),
    )
    op.create_index('ix_building_link_candidate_listing_id', 'building_link_candidate',
                    ['listing_id'], unique=False)
    op.create_index('idx_building_link_candidate_listing_rank', 'building_link_candidate',
                    ['listing_id', 'rank'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_building_link_candidate_listing_rank',
                  table_name='building_link_candidate')
    op.drop_index('ix_building_link_candidate_listing_id',
                  table_name='building_link_candidate')
    op.drop_table('building_link_candidate')
    op.drop_column('building_link', 'candidate_count')
    op.drop_column('building_link', 'confirmed_by_agent')
