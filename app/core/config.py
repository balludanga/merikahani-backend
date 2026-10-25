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

    # Server-side narration: Google's Gemini speech model through a free Google AI Studio key
    # (no card needed). Empty key means the feature is off and the reader's browser voice is
    # used instead. Free-tier text may be used by Google to improve its products, so only
    # PUBLISHED stories are ever sent.
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    # Speech models (comma-separated, in order of preference). The free tier allows only about 10
    # requests a day PER MODEL, so when one is used up the next one takes over.
    TTS_MODELS: str = os.getenv("TTS_MODELS", "gemini-3.8-flash-tts,gemini-3.8-flash-lite-tts,gemini-3.1-flash-tts-preview")
    # Voices to draw from (comma-separated): ids from Google's Indian Hindi voice library, or
    # prebuilt names such as "Sulafat". Each story gets a stable random pick that matches the
    # gender of its narrator and of each character who speaks.
    TTS_FEMALE_VOICES: str = os.getenv(
        "TTS_FEMALE_VOICES", "hi-in-storyteller-2,hi-in-storyteller-6,hi-in-podcaster-9,hi-in-assistant-12"
    )
    TTS_MALE_VOICES: str = os.getenv(
        "TTS_MALE_VOICES", "hi-in-storyteller-1,hi-in-storyteller-5,hi-in-podcaster-8,hi-in-assistant-1"
    )
    # Text models (comma-separated, tried in order) that read a story once to find the narrator's
    # gender and each speaker. Small "lite" models answer in seconds; the bigger ones think longer.
    CAST_MODELS: str = os.getenv("CAST_MODELS", "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.5-flash")
    # Longest single turn (one voice's uninterrupted text) before it is cut at a sentence end, and
    # the most characters one speech request reads (one request may hold two voices).
    TTS_CHUNK_CHARS: int = int(os.getenv("TTS_CHUNK_CHARS", "1500"))
    TTS_WINDOW_CHARS: int = int(os.getenv("TTS_WINDOW_CHARS", "3000"))
    # Soft monthly cap on characters sent, a guard on top of Google's own free limits.
    TTS_MONTHLY_CHARS: int = int(os.getenv("TTS_MONTHLY_CHARS", "300000"))
    # A single story longer than this is not narrated on the server.
    TTS_MAX_STORY_CHARS: int = int(os.getenv("TTS_MAX_STORY_CHARS", "12000"))

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