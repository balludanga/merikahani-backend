---
kind: configuration_system
name: Pydantic Settings-Based Configuration with .env and Environment Variables
category: configuration_system
scope:
    - '**'
source_files:
    - app/core/config.py
    - .env.example
    - app/main.py
    - app/database.py
    - app/core/security.py
    - app/api/endpoints/auth.py
    - app/api/endpoints/seo.py
---

## Approach

The application uses **pydantic-settings** (`BaseSettings`) as its configuration framework. A single `Settings` class in `app/core/config.py` declares all runtime configuration fields with defaults, and a module-level `settings = Settings()` singleton is imported wherever configuration is needed.

Configuration sources are layered (highest precedence wins):
1. **Environment variables** — any field whose default calls `os.getenv(...)` is overridable via the process environment.
2. **`.env` file** — loaded automatically by pydantic-settings via `Config.env_file`, which points to `<repo_root>/.env` using a path computed relative to `config.py` so it works regardless of cwd.
3. **Hardcoded defaults** — every field has a sensible default defined directly on the class.

There is no YAML/JSON/TOML config loader, no feature-flag library, and no secret manager integration beyond reading env vars.

## Key Files

- `app/core/config.py` — sole source of truth for the `Settings` model and the `settings` singleton.
- `.env.example` — template documenting required/optional env vars; not loaded at runtime (no `.env` committed).
- `app/main.py` — constructs the FastAPI app and wires CORS using `settings.ALLOW_ORIGINS`, `settings.HOST`, `settings.PORT`, `settings.app_name`.
- `app/database.py` — reads `settings.DATABASE_URL` to build the SQLAlchemy engine (with SQLite vs PostgreSQL branching logic based on URL prefix).
- `app/core/security.py` — reads `settings.SECRET_KEY` and `settings.ALGORITHM` for JWT encode/decode.
- `app/api/endpoints/auth.py` — reads `settings.GOOGLE_CLIENT_ID` and `settings.ACCESS_TOKEN_EXPIRE_MINUTES`.
- `app/api/endpoints/seo.py` — reads `settings.FRONTEND_URL` for sitemap/RSS base URLs.

## Architecture & Conventions

- **Single settings object**: Every consumer imports `from app.core.config import settings`; there is no dependency injection of config objects.
- **Field naming**: All env-var-backed fields use UPPER_SNAKE_CASE (e.g. `DATABASE_URL`, `SECRET_KEY`, `GOOGLE_CLIENT_ID`, `FRONTEND_URL`, `ACCESS_TOKEN_EXPIRE_MINUTES`).
- **Defaults strategy**: Sensitive values (`SECRET_KEY`, `GOOGLE_CLIENT_ID`) ship with placeholder defaults that explicitly warn against production use; non-sensitive values (`HOST=0.0.0.0`, `PORT=8000`, `ALGORITHM=HS256`, `ALLOW_ORIGINS=[...]`) have real defaults.
- **Boolean parsing**: `debug` is parsed from `DEBUG` via `os.getenv("DEBUG", "true").lower() == "true"` — a manual string-to-bool conversion rather than relying on pydantic's built-in type coercion.
- **CORS origins**: `ALLOW_ORIGINS` is a typed `List[str]` with a static list including localhost, Vercel preview domains, and an ngrok dev URL. In `main.py`, an additional `allow_origin_regex=r"https://.*\.vercel\.app"` is passed to `CORSMiddleware` to cover dynamic Vercel deployment subdomains.
- **Extra fields ignored**: `Config.extra = "ignore"` means unknown keys in `.env` are silently dropped rather than raising errors.
- **Database selection**: The comment in `config.py` states the intent: defaults to SQLite for local, PostgreSQL for production. `database.py` branches on `settings.DATABASE_URL.startswith("sqlite")` to choose between `create_engine` and `create_async_engine`.

## Conventions & Constraints

- **Convention**: New configuration fields belong in `app/core/config.py` inside the `Settings` class, declared with a Python type annotation and a default value.
- **Convention**: To make a field overridable via environment, wrap its default in `os.getenv("ENV_VAR_NAME", <default>)`.
- **Constraint enforced by pydantic-settings**: Unknown keys in the `.env` file are ignored (`extra = "ignore"`), so typos in `.env` will be silently swallowed — they do not raise a validation error.
- **Constraint enforced by code**: `SECRET_KEY` must be set before running in production; the default string `"your-secret-key-here-change-in-production"` is a clear warning but is not validated at startup.
- **Constraint enforced by code**: `GOOGLE_CLIENT_ID` is read at request time in `auth.py`; if unset, Google OAuth flows will fail at runtime rather than at import time.
- **Convention**: The `.env.example` file documents the expected shape of `.env` but is not itself loaded at runtime (it is excluded by `.gitignore` patterns implied by its name).
- **No feature flags or per-environment config files**: There is no staging/prod config split; environment selection is implicit (SQLite vs PostgreSQL based on `DATABASE_URL` prefix) rather than explicit.