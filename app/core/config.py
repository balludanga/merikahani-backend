from pydantic_settings import BaseSettings
from typing import List, Optional
import os

class Settings(BaseSettings):
    app_name: str = "लेखनी"
    admin_email: str = "admin@example.com"
    items_per_page: int = 10
    debug: bool = os.getenv("DEBUG", "true").lower() == "true"

    # Database - defaults to SQLite for local, PostgreSQL for production
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'app.db'))}"
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

    # Supabase Storage — media uploads (service key stays server-side only).
    # Empty values mean uploads are disabled; the editor falls back to URL input.
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    UPLOAD_BUCKET: str = os.getenv("UPLOAD_BUCKET", "lekhani-media")

    # IndexNow (Bing, Yandex, ...): tells search engines about new/changed/removed stories at once.
    # The key is public by design - it must also be served at <FRONTEND_URL>/<key>.txt.
    INDEXNOW_KEY: str = os.getenv("INDEXNOW_KEY", "4740513ae79e931a49c79fb66dd01fee")
    # None = follow DEBUG (on in production, off locally and in tests); "true"/"false" forces it.
    INDEXNOW_ENABLED: Optional[bool] = (
        None if os.getenv("INDEXNOW_ENABLED", "") == "" else os.getenv("INDEXNOW_ENABLED", "").lower() in ("1", "true")
    )

    # Who may open the owner's status page: comma-separated emails. Only an account whose
    # address Google has verified counts, and an empty list means nobody can.
    ADMIN_EMAILS: str = os.getenv("ADMIN_EMAILS", "")

    # Public frontend URL (used for sitemap / RSS links)
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "https://lekhanii.vercel.app")

    # CORS - allow both local and production domains
    # Local dev origins; main.py only honours these when DEBUG is on.
    ALLOW_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
    ]

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    class Config:
        # Ensure we load the backend/.env file regardless of current working directory.
        env_file = str(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))
        extra = "ignore"  # Ignore extra fields from .env file

settings = Settings()