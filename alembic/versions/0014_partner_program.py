"""Write and earn: partner_applications, earning_months, earning_lines and payouts."""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()

    if "partner_applications" not in tables:
        op.create_table(
            "partner_applications",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(12), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("followers_at_apply", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("stories_at_apply", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("applied_at", sa.DateTime(), nullable=False),
            sa.Column("decided_at", sa.DateTime(), nullable=True),
            sa.Column("decided_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("decision_note", sa.Text(), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("payout_upi", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("user_id", name="uq_partner_user"),
        )
        op.create_index("ix_partner_applications_status", "partner_applications", ["status"])

    if "earning_months" not in tables:
        op.create_table(
            "earning_months",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("month", sa.String(7), nullable=False, unique=True),
            sa.Column("pool_paise", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_per_point_paise", sa.Integer(), nullable=True),
            sa.Column("finalized_at", sa.DateTime(), nullable=True),
            sa.Column("finalized_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("total_points", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("distributed_paise", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "earning_lines" not in tables:
        op.create_table(
            "earning_lines",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("month_id", sa.Integer(), sa.ForeignKey("earning_months.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("claps", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("highlights", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("responses", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("readers", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("points", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("amount_paise", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("held_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("month_id", "user_id", name="uq_earning_month_user"),
        )
        op.create_index("ix_earning_lines_month_id", "earning_lines", ["month_id"])
        op.create_index("ix_earning_lines_user_id", "earning_lines", ["user_id"])

    if "payouts" not in tables:
        op.create_table(
            "payouts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("amount_paise", sa.Integer(), nullable=False),
            sa.Column("reference", sa.String(80), nullable=True),
            sa.Column("paid_at", sa.DateTime(), nullable=False),
            sa.Column("paid_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        )
        op.create_index("ix_payouts_user_id", "payouts", ["user_id"])


def downgrade() -> None:
    pass
