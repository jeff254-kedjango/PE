"""billing_tables (payment_intent + payment_ledger)

Adds the two billing tables for the listing-location access model
(PE/billing_architecture.md §3). Additive only — the include_object allow-list in
env.py restricts autogenerate to MANAGED_TABLES, and these CREATEs touch no existing
table. payment_ledger is append-only (UNIQUE mpesa_receipt is the idempotency anchor);
we add the same BEFORE-UPDATE/DELETE guard trigger used for notification_audit on
Postgres so the money record can't be tampered with. The trigger block is wrapped so
it is a no-op on SQLite (tests) and only fires on Postgres.

Revision ID: a1b2c3d4e5f6
Revises: e15260c80e9d
Create Date: 2026-06-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'e15260c80e9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'payment_intent',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('phone', sa.String(length=20), nullable=False),
        sa.Column('tier', sa.String(length=8), nullable=False),
        sa.Column('amount_kes', sa.Integer(), nullable=False),
        sa.Column('merchant_request_id', sa.String(length=64), nullable=True),
        sa.Column('checkout_request_id', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('mpesa_receipt', sa.String(length=32), nullable=True),
        sa.Column('result_code', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('checkout_request_id', name='uq_payment_intent_checkout_request'),
        sa.UniqueConstraint('mpesa_receipt', name='uq_payment_intent_receipt'),
    )
    op.create_index('idx_payment_intent_user_status', 'payment_intent', ['user_id', 'status'], unique=False)
    op.create_index(op.f('ix_payment_intent_checkout_request_id'), 'payment_intent', ['checkout_request_id'], unique=False)
    op.create_index(op.f('ix_payment_intent_merchant_request_id'), 'payment_intent', ['merchant_request_id'], unique=False)
    op.create_index(op.f('ix_payment_intent_status'), 'payment_intent', ['status'], unique=False)
    op.create_index(op.f('ix_payment_intent_user_id'), 'payment_intent', ['user_id'], unique=False)
    op.create_index(op.f('ix_payment_intent_created_at'), 'payment_intent', ['created_at'], unique=False)

    op.create_table(
        'payment_ledger',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('intent_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('mpesa_receipt', sa.String(length=32), nullable=False),
        sa.Column('amount_kes', sa.Integer(), nullable=False),
        sa.Column('tier', sa.String(length=8), nullable=False),
        sa.Column('quota', sa.Integer(), nullable=False),
        sa.Column('window_seconds', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['intent_id'], ['payment_intent.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('mpesa_receipt', name='uq_payment_ledger_receipt'),
    )
    op.create_index(op.f('ix_payment_ledger_intent_id'), 'payment_ledger', ['intent_id'], unique=False)
    op.create_index(op.f('ix_payment_ledger_user_id'), 'payment_ledger', ['user_id'], unique=False)
    op.create_index(op.f('ix_payment_ledger_created_at'), 'payment_ledger', ['created_at'], unique=False)

    # Append-only enforcement on Postgres (same pattern as notification_audit). The DO
    # block is Postgres-only; skip entirely on other backends (SQLite tests).
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
        CREATE OR REPLACE FUNCTION payment_ledger_append_only()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'payment_ledger is append-only (no % allowed)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """)
        op.execute("""
        CREATE TRIGGER trg_payment_ledger_no_update
        BEFORE UPDATE OR DELETE OR TRUNCATE ON payment_ledger
        FOR EACH STATEMENT EXECUTE FUNCTION payment_ledger_append_only();
        """)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_payment_ledger_no_update ON payment_ledger;")
        op.execute("DROP FUNCTION IF EXISTS payment_ledger_append_only();")
    op.drop_table('payment_ledger')
    op.drop_table('payment_intent')
