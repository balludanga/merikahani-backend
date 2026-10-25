"""story_audio.timings: when each word of the narration is spoken (follow-along highlight)."""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = [c["name"] for c in sa.inspect(op.get_bind()).get_columns("story_audio")]
    if "timings" not in columns:
        op.add_column("story_audio", sa.Column("timings", sa.Text(), nullable=True))


def downgrade() -> None:
    pass
