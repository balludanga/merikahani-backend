"""Run Alembic migrations at startup.

Every revision is idempotent (it inspects the schema first), so the same chain
works for a brand-new database, for the production database that was built by
create_all() plus hand-written ALTERs before Alembic existed, and for re-runs.
"""

import os

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.database import engine

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# Arbitrary constant: serialises concurrent starts (Postgres only).
LOCK_ID = 7_272_727_001


def run_migrations() -> None:
    config = Config(os.path.join(BASE_DIR, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BASE_DIR, "alembic"))
    with engine.connect() as connection:
        is_postgres = connection.dialect.name == "postgresql"
        if is_postgres:
            connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": LOCK_ID})
            connection.commit()
        try:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()
        finally:
            if is_postgres:
                connection.rollback()
                connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": LOCK_ID})
                connection.commit()
