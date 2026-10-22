"""Post columns that older databases lack, plus the indexes the feeds rely on.

Replaces the hand-written ALTER TABLE / CREATE INDEX that used to run at startup.
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

INDEXES = [
    ("ix_posts_published", "posts", "published"),
    ("ix_posts_created_at", "posts", "created_at"),
    ("ix_posts_author_id", "posts", "author_id"),
    ("ix_posts_topic", "posts", "topic"),
    ("ix_comments_post_id", "comments", "post_id"),
    ("ix_comments_author_id", "comments", "author_id"),
    ("ix_claps_post_id", "claps", "post_id"),
    ("ix_post_tags_tag_id", "post_tags", "tag_id"),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns("posts")}
    if "topic" not in existing:
        op.add_column("posts", sa.Column("topic", sa.String(), nullable=True))
    if "clap_count" not in existing:
        op.add_column("posts", sa.Column("clap_count", sa.Integer(), nullable=True, server_default="0"))
    if "content_html" not in existing:
        op.add_column("posts", sa.Column("content_html", sa.Text(), nullable=True))
    for name, table, column in INDEXES:
        op.execute(sa.text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})"))


def downgrade() -> None:
    pass
