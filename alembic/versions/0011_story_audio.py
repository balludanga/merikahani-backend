"""story_audio + tts_usage: cached narration of stories and the monthly quota counter."""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "story_audio" not in tables:
        op.create_table(
            "story_audio",
            sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("voice", sa.String(60), nullable=False),
            sa.Column("audio", sa.LargeBinary(), nullable=False),
            sa.Column("chars", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    if "tts_usage" not in tables:
        op.create_table(
            "tts_usage",
            sa.Column("month", sa.String(7), primary_key=True),
            sa.Column("chars", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    pass
