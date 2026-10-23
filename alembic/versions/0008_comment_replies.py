"""Responses can be answered: comments.parent_id points at the response being answered
(always a top-level one, so threads are one level deep)."""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "parent_id" not in {col["name"] for col in inspector.get_columns("comments")}:
        op.add_column("comments", sa.Column("parent_id", sa.Integer(), nullable=True))
        # SQLite cannot add a constraint to an existing table; there the model's FK only
        # applies to fresh databases, and the API deletes replies itself.
        if bind.dialect.name != "sqlite":
            op.create_foreign_key("fk_comments_parent_id", "comments", "comments", ["parent_id"], ["id"], ondelete="CASCADE")
    if "ix_comments_parent_id" not in {ix["name"] for ix in sa.inspect(bind).get_indexes("comments")}:
        op.create_index("ix_comments_parent_id", "comments", ["parent_id"])


def downgrade() -> None:
    pass
