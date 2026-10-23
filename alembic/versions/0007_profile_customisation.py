"""Profile customisation: tagline, status, location, accent colour, banner, genres,
links, pinned stories, and the writer's own photo/banner (user_media)."""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

USER_COLUMNS = [
    ("tagline", sa.String(120)),
    ("status_line", sa.String(120)),
    ("location", sa.String(80)),
    ("accent", sa.String(7)),
    ("cover_style", sa.String(24)),
    ("cover_url", sa.String()),
    ("genres_json", sa.Text()),
    ("links_json", sa.Text()),
    ("pinned_json", sa.Text()),
]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {col["name"] for col in inspector.get_columns("users")}
    for name, kind in USER_COLUMNS:
        if name not in existing:
            op.add_column("users", sa.Column(name, kind, nullable=True))
    if "user_media" not in inspector.get_table_names():
        op.create_table(
            "user_media",
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
            sa.Column("avatar", sa.LargeBinary(), nullable=True),
            sa.Column("avatar_type", sa.String(20), nullable=True),
            sa.Column("avatar_version", sa.Integer(), nullable=True),
            sa.Column("cover", sa.LargeBinary(), nullable=True),
            sa.Column("cover_type", sa.String(20), nullable=True),
            sa.Column("cover_version", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    pass
