"""Narration audio may live outside the database: where it is, under what key, and how big it is."""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {col["name"]: col for col in sa.inspect(op.get_bind()).get_columns("story_audio")}
    if "storage" not in columns:
        op.add_column("story_audio", sa.Column("storage", sa.String(12), nullable=False, server_default="db"))
    if "blob_key" not in columns:
        op.add_column("story_audio", sa.Column("blob_key", sa.String(120), nullable=True))
    if "size_bytes" not in columns:
        op.add_column("story_audio", sa.Column("size_bytes", sa.Integer(), nullable=True))
    # The bytes may be elsewhere now, so the column can be empty. (Batch mode rebuilds the table on
    # SQLite; on Postgres it is a plain, instant ALTER ... DROP NOT NULL.)
    if not columns["audio"]["nullable"]:
        with op.batch_alter_table("story_audio") as batch:
            batch.alter_column("audio", existing_type=sa.LargeBinary(), nullable=True)
    # Every narration made so far is in the database; record how big each one is.
    op.execute(sa.text("UPDATE story_audio SET size_bytes = length(audio) WHERE size_bytes IS NULL AND audio IS NOT NULL"))


def downgrade() -> None:
    pass
