"""posts.language, users.read_languages_json / write_languages_json: stories in any of the 22 scheduled
languages and English, and the languages a person reads and writes in.

Every story written before this is Hindi, so the column starts as 'hi' for all of them."""
import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    post_columns = {c["name"] for c in inspector.get_columns("posts")}
    if "language" not in post_columns:
        op.add_column("posts", sa.Column("language", sa.String(8), nullable=False, server_default="hi"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_posts_language ON posts (language)"))
    user_columns = {c["name"] for c in inspector.get_columns("users")}
    if "read_languages_json" not in user_columns:
        op.add_column("users", sa.Column("read_languages_json", sa.Text(), nullable=True))
    if "write_languages_json" not in user_columns:
        op.add_column("users", sa.Column("write_languages_json", sa.Text(), nullable=True))


def downgrade() -> None:
    pass
