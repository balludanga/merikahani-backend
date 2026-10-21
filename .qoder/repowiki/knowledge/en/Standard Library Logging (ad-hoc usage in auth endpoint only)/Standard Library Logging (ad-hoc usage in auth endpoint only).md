---
kind: logging_system
name: Standard Library Logging (ad-hoc usage in auth endpoint only)
category: logging_system
scope:
    - '**'
source_files:
    - app/api/endpoints/auth.py
---

## What system/approach is used

The repository uses Python's standard library `logging` module. There is no third-party logging framework (no structlog, loguru, sentry_sdk, or similar). No centralized logger configuration exists — there are no calls to `logging.basicConfig`, `logging.config.dictConfig`, or any other initialization routine anywhere in the codebase.

## Key files and packages

- `app/api/endpoints/auth.py` — the only file that imports and uses `logging`. It creates a per-module logger via `logger = logging.getLogger(__name__)` and emits `info`, `warning`, and `error` messages throughout the Google login flow and the `/me` endpoint.

No other file under `app/` (endpoints, core, models, schemas) references logging.

## Architecture and conventions

- **Per-module logger**: The single usage follows the standard pattern of `logging.getLogger(__name__)` at module top level (`auth.py` line 14).
- **Log levels used**: `logger.info` for normal flow events (login attempt, token verification, user creation), `logger.warning` for validation failures (missing fields, non-object JSON body), and `logger.error` for failure paths (JSON parse errors, external API failures, audience mismatch).
- **Message format**: All log records are plain formatted strings built with f-strings; there is no structured logging (no dict payloads, no JSON serialization, no correlation/request IDs attached).
- **No central configuration**: Because `basicConfig` / `dictConfig` is never called, log output defaults to Python's default stderr handler with `WARNING` level and the default `%(levelname)s:%(name)s:%(message)s` formatter. This means `info` and `debug` logs emitted by `auth.py` will not appear unless the process is invoked with an environment variable such as `PYTHONWARNINGS=always` or the runtime adjusts the root logger externally.
- **Sensitive data handling**: Some log lines include partial secrets (e.g., `google_client_id[:10]...`) but also include full email addresses and token payload fields, so no redaction policy is enforced in code.

## Conventions and constraints

- Observed convention: module-level loggers are created with `logging.getLogger(__name__)` (seen in `app/api/endpoints/auth.py`).
- Observed convention: log messages are plain f-string messages rather than structured dicts.
- Constraint (implicit): because no `basicConfig` or equivalent is called, the effective log level is `WARNING`; `info`-level statements in this repo produce no output without external configuration.
- No cross-cutting logging middleware, request ID propagation, or FastAPI integration layer was found.