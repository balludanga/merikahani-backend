"""Baseline: create any table that does not exist yet.

Databases that predate Alembic already have these tables (they were built by
create_all), so this is a no-op for them; a fresh database gets the full schema.
Columns added later are handled by the following revisions, which check first.
"""
from alembic import op

from app.database import Base
from app import models  # noqa: F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    pass
