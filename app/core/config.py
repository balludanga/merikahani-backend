from pydantic_settings import BaseSettings
from typing import List, Optional
import os

class Settings(BaseSettings):
    app_name: str = "लेखनी"
    admin_email: str = "admin@example.com"
    items_per_page: int = 10
    # Fail closed. Debug mode publishes /docs and /openapi.json, trusts the localhost
    # CORS origins and turns IndexNow off, so a deployment that simply forgot the
    # variable must not land in it. Local runs, docker-compose and the tests set
    # DEBUG=true explicitly.
    debug: bool = os.getenv("DEBUG", "false").lower() in ("1", "true")

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
    # requests a day PER MODEL, so when one is used up the next one takes over. (Not
    # gemini-3.1-flash-tts-preview: it refuses the speech_metadata style/speaker annotations.)
    TTS_MODELS: str = os.getenv("TTS_MODELS", "gemini-3.8-flash-tts,gemini-3.8-flash-lite-tts")
    # (storyteller-5 and podcaster-8 were measured to be bursty - fast inside a sentence, then a 1.4 s
    # stop at every full stop - and were left out of the male pool; add them back here if wanted.)
    # Voices to draw from (comma-separated): ids from Google's Indian Hindi voice library, or
    # prebuilt names such as "Sulafat". Each story gets a stable random pick that matches the
    # gender of its narrator and of each character who speaks.
    TTS_FEMALE_VOICES: str = os.getenv(
        "TTS_FEMALE_VOICES", "hi-in-storyteller-2,hi-in-storyteller-6,hi-in-podcaster-9,hi-in-assistant-12"
    )
    TTS_MALE_VOICES: str = os.getenv(
        "TTS_MALE_VOICES", "hi-in-storyteller-1,hi-in-assistant-1"
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

    # Write and earn (the partner programme). A writer may apply once they have this many
    # followers and published stories; the review team then decides. After a decline they wait
    # PARTNER_REAPPLY_DAYS. Only readers whose account is at least PARTNER_READER_MIN_AGE_DAYS old
    # count towards a month's pool. Amounts below PARTNER_MIN_PAYOUT_RUPEES are carried over
    # instead of being paid out.
    PARTNER_MIN_FOLLOWERS: int = int(os.getenv("PARTNER_MIN_FOLLOWERS", "200"))
    PARTNER_MIN_STORIES: int = int(os.getenv("PARTNER_MIN_STORIES", "1"))
    PARTNER_REAPPLY_DAYS: int = int(os.getenv("PARTNER_REAPPLY_DAYS", "30"))
    PARTNER_READER_MIN_AGE_DAYS: int = int(os.getenv("PARTNER_READER_MIN_AGE_DAYS", "7"))
    PARTNER_MIN_PAYOUT_RUPEES: int = int(os.getenv("PARTNER_MIN_PAYOUT_RUPEES", "100"))

    # Who owns the site: comma-separated emails, Google-verified like ADMIN_EMAILS. An owner is also a
    # reviewer, and is the only one who can open the money pages (the monthly sheet, the books, the
    # payment log). Empty means nobody can.
    OWNER_EMAILS: str = os.getenv("OWNER_EMAILS", "")

    # Payments. Empty = the site takes no money and shows nothing about it. "sandbox" is an offline
    # stand-in for development and tests (refused when DEBUG is off). A real provider is switched on by
    # name once its adapter is connected (see app/core/payments/providers/__init__.py). The three
    # credentials below are provider-neutral: whichever adapter is connected reads them.
    PAYMENTS_PROVIDER: str = os.getenv("PAYMENTS_PROVIDER", "").strip().lower()
    PAYMENTS_API_KEY: str = os.getenv("PAYMENTS_API_KEY", "")
    PAYMENTS_API_SECRET: str = os.getenv("PAYMENTS_API_SECRET", "")
    PAYMENTS_WEBHOOK_SECRET: str = os.getenv("PAYMENTS_WEBHOOK_SECRET", "")
    # Signs the sandbox's pretend webhooks. Worthless on purpose: the sandbox cannot run in production.
    PAYMENTS_SANDBOX_SECRET: str = os.getenv("PAYMENTS_SANDBOX_SECRET", "sandbox-secret-for-development-only")

    # Tips: the smallest and largest tip, and the quick-pick amounts shown to the reader (rupees).
    TIP_MIN_RUPEES: int = int(os.getenv("TIP_MIN_RUPEES", "10"))
    TIP_MAX_RUPEES: int = int(os.getenv("TIP_MAX_RUPEES", "10000"))
    TIP_PRESET_RUPEES: str = os.getenv("TIP_PRESET_RUPEES", "20,50,100")
    # What the site keeps of each tip, in basis points (1000 = 10%); the rest is the writer's.
    TIP_SITE_FEE_BPS: int = int(os.getenv("TIP_SITE_FEE_BPS", "1000"))

    # Membership prices (rupees, tax included). GST_PERCENT is the GST inside those prices: 0 while the
    # site is not registered for it.
    MEMBERSHIP_MONTHLY_RUPEES: int = int(os.getenv("MEMBERSHIP_MONTHLY_RUPEES", "49"))
    MEMBERSHIP_YEARLY_RUPEES: int = int(os.getenv("MEMBERSHIP_YEARLY_RUPEES", "399"))
    GST_PERCENT: int = int(os.getenv("GST_PERCENT", "0"))

    # How many points a reader's reaction is worth, by whether the reader held a membership at that
    # moment (whole numbers; each month can override both). 1 and 1 changes nothing; 1 and 0 counts
    # members only. Applied to the month's pool only, never retroactively to a finalized month.
    POINTS_MEMBER_WEIGHT: int = int(os.getenv("POINTS_MEMBER_WEIGHT", "1"))
    POINTS_NONMEMBER_WEIGHT: int = int(os.getenv("POINTS_NONMEMBER_WEIGHT", "1"))

    # Where narration MP3s are kept: "db" (in the story_audio row, as before), "local" (a folder, for
    # development or a host with a persistent disk) or "remote" (an object-storage service, once its
    # adapter is connected: app/core/blobstore.py). If the chosen place is not usable the audio simply
    # stays in the database, so narration never breaks over this.
    AUDIO_STORAGE: str = os.getenv("AUDIO_STORAGE", "db").strip().lower()
    AUDIO_LOCAL_DIR: str = os.getenv(
        "AUDIO_LOCAL_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "audio_store"))
    )
    # Credentials for the remote object store (provider-neutral names, like the payments ones).
    OBJECT_STORE_ENDPOINT: str = os.getenv("OBJECT_STORE_ENDPOINT", "")
    OBJECT_STORE_BUCKET: str = os.getenv("OBJECT_STORE_BUCKET", "")
    OBJECT_STORE_ACCESS_KEY: str = os.getenv("OBJECT_STORE_ACCESS_KEY", "")
    OBJECT_STORE_SECRET_KEY: str = os.getenv("OBJECT_STORE_SECRET_KEY", "")
    OBJECT_STORE_REGION: str = os.getenv("OBJECT_STORE_REGION", "")

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