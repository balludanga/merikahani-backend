"""Money: payments, webhook log, memberships, the books, the profit sheet, and the columns that tie them to write and earn."""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def _add_column(table: str, column: sa.Column) -> None:
    existing = {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}
    if column.name not in existing:
        op.add_column(table, column)


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()

    if "payments" not in tables:
        op.create_table(
            "payments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("public_id", sa.String(32), nullable=False, unique=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("purpose", sa.String(12), nullable=False),
            sa.Column("status", sa.String(14), nullable=False),
            sa.Column("amount_paise", sa.BigInteger(), nullable=False),
            sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
            sa.Column("provider", sa.String(20), nullable=False),
            sa.Column("provider_order_id", sa.String(80), nullable=True),
            sa.Column("provider_payment_id", sa.String(80), nullable=True),
            sa.Column("provider_subscription_id", sa.String(80), nullable=True),
            sa.Column("target_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id", ondelete="SET NULL"), nullable=True),
            sa.Column("message", sa.String(200), nullable=True),
            sa.Column("anonymous", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("plan_code", sa.String(16), nullable=True),
            sa.Column("writer_share_paise", sa.BigInteger(), nullable=True),
            sa.Column("site_fee_paise", sa.BigInteger(), nullable=True),
            sa.Column("tax_paise", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("fee_paise", sa.BigInteger(), nullable=True),
            sa.Column("refunded_paise", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("refunded_primary_paise", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("failure_reason", sa.String(200), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("captured_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("provider", "provider_order_id", name="uq_payment_provider_order"),
            sa.UniqueConstraint("provider", "provider_payment_id", name="uq_payment_provider_payment"),
        )
        op.create_index("ix_payments_id", "payments", ["id"])
        op.create_index("ix_payments_user_id", "payments", ["user_id"])
        op.create_index("ix_payments_status", "payments", ["status"])
        op.create_index("ix_payments_target_user_id", "payments", ["target_user_id"])
        op.create_index("ix_payments_provider_subscription_id", "payments", ["provider_subscription_id"])

    if "payment_events" not in tables:
        op.create_table(
            "payment_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("provider", sa.String(20), nullable=False),
            sa.Column("event_id", sa.String(120), nullable=False),
            sa.Column("type", sa.String(40), nullable=False),
            sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(10), nullable=False),
            sa.Column("detail", sa.String(300), nullable=True),
            sa.Column("payload", sa.Text(), nullable=True),
            sa.Column("received_at", sa.DateTime(), nullable=False),
            sa.Column("processed_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("provider", "event_id", name="uq_payment_event"),
        )
        op.create_index("ix_payment_events_id", "payment_events", ["id"])
        op.create_index("ix_payment_events_payment_id", "payment_events", ["payment_id"])
        op.create_index("ix_payment_events_status", "payment_events", ["status"])
        op.create_index("ix_payment_events_received_at", "payment_events", ["received_at"])

    if "memberships" not in tables:
        op.create_table(
            "memberships",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("plan_code", sa.String(16), nullable=False),
            sa.Column("status", sa.String(10), nullable=False),
            sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("provider_subscription_id", sa.String(80), nullable=True),
            sa.Column("show_badge", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("cancelled_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("user_id", name="uq_membership_user"),
        )
        op.create_index("ix_memberships_id", "memberships", ["id"])
        op.create_index("ix_memberships_provider_subscription_id", "memberships", ["provider_subscription_id"])

    if "membership_periods" not in tables:
        op.create_table(
            "membership_periods",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id"), nullable=False),
            sa.Column("starts_at", sa.DateTime(), nullable=False),
            sa.Column("ends_at", sa.DateTime(), nullable=False),
            sa.Column("voided_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("payment_id", name="uq_period_payment"),
        )
        op.create_index("ix_membership_periods_id", "membership_periods", ["id"])
        op.create_index("ix_membership_periods_user_id", "membership_periods", ["user_id"])

    if "ledger_transactions" not in tables:
        op.create_table(
            "ledger_transactions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("key", sa.String(80), nullable=False, unique=True),
            sa.Column("kind", sa.String(24), nullable=False),
            sa.Column("memo", sa.String(200), nullable=True),
            sa.Column("posted_at", sa.DateTime(), nullable=False),
            sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id"), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("reverses_id", sa.Integer(), sa.ForeignKey("ledger_transactions.id"), nullable=True, unique=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_ledger_transactions_id", "ledger_transactions", ["id"])
        op.create_index("ix_ledger_transactions_kind", "ledger_transactions", ["kind"])
        op.create_index("ix_ledger_transactions_posted_at", "ledger_transactions", ["posted_at"])
        op.create_index("ix_ledger_transactions_payment_id", "ledger_transactions", ["payment_id"])

    if "ledger_entries" not in tables:
        op.create_table(
            "ledger_entries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("txn_id", sa.Integer(), sa.ForeignKey("ledger_transactions.id"), nullable=False),
            sa.Column("account", sa.String(32), nullable=False),
            sa.Column("amount_paise", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        )
        op.create_index("ix_ledger_entries_id", "ledger_entries", ["id"])
        op.create_index("ix_ledger_entries_txn_id", "ledger_entries", ["txn_id"])
        op.create_index("ix_ledger_entries_account", "ledger_entries", ["account"])
        op.create_index("ix_ledger_entries_user_id", "ledger_entries", ["user_id"])

    if "profit_sheets" not in tables:
        op.create_table(
            "profit_sheets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("month", sa.String(7), nullable=False),
            sa.Column("pool_percent", sa.Integer(), nullable=True),
            sa.Column("holdback_percent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("month", name="uq_profit_sheet_month"),
        )
        op.create_index("ix_profit_sheets_id", "profit_sheets", ["id"])

    # Write and earn learns about the money.
    _add_column("partner_applications", sa.Column("accepts_tips", sa.Boolean(), nullable=False, server_default=sa.true()))
    _add_column("earning_months", sa.Column("member_weight", sa.Integer(), nullable=True))
    _add_column("earning_months", sa.Column("nonmember_weight", sa.Integer(), nullable=True))
    _add_column("earning_lines", sa.Column("member_points", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    pass
