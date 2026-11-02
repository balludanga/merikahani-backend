"""posts.moderation_state + review_rounds + review_notes: reviewers send a story back with notes."""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = [c["name"] for c in inspector.get_columns("posts")]
    if "moderation_state" not in columns:
        op.add_column(
            "posts", sa.Column("moderation_state", sa.String(20), nullable=False, server_default="none")
        )
        op.create_index("ix_posts_moderation_state", "posts", ["moderation_state"])
    tables = inspector.get_table_names()
    if "review_rounds" not in tables:
        op.create_table(
            "review_rounds",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_review_rounds_post_id", "review_rounds", ["post_id"])
    if "review_notes" not in tables:
        op.create_table(
            "review_notes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("round_id", sa.Integer(), sa.ForeignKey("review_rounds.id", ondelete="SET NULL"), nullable=True),
            sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("start_offset", sa.Integer(), nullable=True),
            sa.Column("prefix", sa.String(80), nullable=True),
            sa.Column("suffix", sa.String(80), nullable=True),
            sa.Column("category", sa.String(16), nullable=False, server_default="other"),
            sa.Column("comment", sa.Text(), nullable=False),
            sa.Column("suggestion", sa.Text(), nullable=True),
            sa.Column("status", sa.String(8), nullable=False, server_default="open"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_review_notes_post_id", "review_notes", ["post_id"])
        op.create_index("ix_review_notes_round_id", "review_notes", ["round_id"])


def downgrade() -> None:
    pass
