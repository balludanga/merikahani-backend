"""Alembic environment: always runs online against the app's own engine."""

from alembic import context

from app.database import Base, engine
from app import models  # noqa: F401  register ORM models


def run_migrations_online() -> None:
    # app/migrations.py passes an open connection (so it can hold a lock around the run).
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _migrate(connection)
        return
    with engine.connect() as conn:
        _migrate(conn)


def _migrate(connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported; run against a database.")
run_migrations_online()
