import re

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings

# Normalize Postgres URLs to the explicit psycopg2 dialect (installed via
# psycopg2-binary). Covers Render's "postgresql://", the legacy "postgres://"
# scheme, and the "postgresql+psycopg://" (driver v3) form, so the engine
# never selects an uninstalled driver.
database_url = settings.DATABASE_URL
if database_url.startswith("postgres"):
    database_url = re.sub(
        r"^postgres(?:ql)?(?:\+psycopg(?:2)?)?://",
        "postgresql+psycopg2://",
        database_url,
    )

# Configure connection args based on database type
connect_args = {}
if database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    database_url,
    connect_args=connect_args,
    pool_pre_ping=True,  # Verify connections before using
    pool_recycle=300,  # Recycle connections after 5 minutes
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

