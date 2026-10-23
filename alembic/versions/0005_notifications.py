"""Notifications: tell an author when a reader comments, claps or highlights."""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "notifications" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "notifications",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id"), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("comment_id", sa.Integer(), nullable=True),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("read_at", sa.DateTime(), nullable=True),
        )
    for name, column in (
        ("ix_notifications_id", "id"),
        ("ix_notifications_user_id", "user_id"),
        ("ix_notifications_post_id", "post_id"),
        ("ix_notifications_created_at", "created_at"),
    ):
        op.execute(sa.text(f"CREATE INDEX IF NOT EXISTS {name} ON notifications ({column})"))


def downgrade() -> None:
    pass
