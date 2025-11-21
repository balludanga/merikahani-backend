from pydantic_settings import BaseSettings
from typing import List
import os

class Settings(BaseSettings):
    app_name: str = "My Fullstack Project"
    admin_email: str = "admin@example.com"
    items_per_page: int = 10
    debug: bool = os.getenv("DEBUG", "true").lower() == "true"

    # Database - defaults to SQLite for local, PostgreSQL for production
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./app.db"
    )

    # Security
    SECRET_KEY: str = os.getenv(
        "SECRET_KEY",
        "your-secret-key-here-change-in-production"
    )
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Google OAuth
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "YOUR_GOOGLE_CLIENT_ID")

    # CORS - allow both local and production domains
    ALLOW_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "https://*.vercel.app",  # All Vercel preview deployments
    ]

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    class Config:
        env_file = ".env"
        extra = "ignore"  # Ignore extra fields from .env file

settings = Settings()