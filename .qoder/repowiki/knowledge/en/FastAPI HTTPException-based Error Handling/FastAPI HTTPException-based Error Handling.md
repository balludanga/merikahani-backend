---
kind: error_handling
name: FastAPI HTTPException-based Error Handling
category: error_handling
scope:
    - '**'
source_files:
    - app/main.py
    - app/core/dependencies.py
    - app/api/endpoints/auth.py
    - app/api/endpoints/comments.py
    - app/api/endpoints/bookmarks.py
    - app/api/endpoints/follows.py
    - app/api/endpoints/tags.py
    - app/api/endpoints/users.py
---

## Approach

The Lekhani backend uses FastAPI's built-in `HTTPException` as its sole error signaling mechanism. There is no custom exception hierarchy, no sentinel error types, and no global exception handler registered via `@app.exception_handler`. Errors are raised directly from endpoint functions and dependency callables, and FastAPI converts them into JSON responses with the appropriate HTTP status code.

## Key Files

- `app/main.py` — Application factory; registers CORSMiddleware plus two ASGI middlewares (`CoopHeaderMiddleware`, `PrivateNetworkMiddleware`). No exception handlers are attached here.
- `app/core/dependencies.py` — Central place for auth-related error handling: `get_current_user` raises `HTTPException(401, ...)` with a `WWW-Authenticate: Bearer` header on invalid/expired JWTs; `get_current_user_optional` catches `HTTPException` and returns `None` instead of propagating it.
- `app/api/endpoints/auth.py` — Raises `HTTPException` for malformed JSON bodies (400), missing Google token (400), invalid Google tokens/client IDs (401), duplicate email/username (400), and wrong credentials (401).
- `app/api/endpoints/comments.py` — Raises 404 for missing posts/comments and 403 when a user tries to delete another user's comment.
- `app/api/endpoints/bookmarks.py`, `follows.py`, `tags.py`, `users.py` — Raise 404/400/401 `HTTPException`s for domain-specific not-found / validation / auth failures.

## Architecture & Conventions

1. **No custom exception types.** All application errors are represented by `fastapi.HTTPException` instances constructed inline at the raise site.
2. **Status codes are chosen per-error:**
   - 400 Bad Request — malformed input, missing fields, business-rule violations (e.g. "आप स्वयं को फ़ॉलो नहीं कर सकते", duplicate email/username).
   - 401 Unauthorized — invalid/expired JWT, wrong password, missing Google token.
   - 403 Forbidden — authorization failure after authentication (e.g. deleting another user's comment).
   - 404 Not Found — resource lookup failures (post/comment/tag/user not found).
3. **Auth middleware pattern in dependencies:** `get_current_user` centralizes JWT decoding and maps `jose.exceptions.ExpiredSignatureError` and `JWTError` to `HTTPException(401)`. A reusable `credentials_exception` object is defined once and reused for generic "Could not validate credentials" cases.
4. **Optional auth dependency:** `get_current_user_optional` wraps `get_current_user` and swallows `HTTPException`, returning `None` so endpoints can branch on an absent user without try/except blocks.
5. **ASGI middlewares swallow exceptions defensively:** Both `CoopHeaderMiddleware` and `PrivateNetworkMiddleware` wrap header mutation in `try/except Exception: pass`, ensuring that setting response headers never breaks the request pipeline.
6. **No global error formatter.** Since no `@app.exception_handler` is registered, FastAPI's default JSON error response shape is used.
7. **Localized error messages.** Many `detail` strings are in Hindi (Devanagari script), e.g. "रचना नहीं मिली", "लेखक नहीं मिला", "ईमेल या पासवर्ड गलत है", while others remain in English ("Post not found", "Not authorized to delete this comment").
8. **Tests exercise error paths** via `TestClient` against the live app rather than unit-testing exceptions directly (see `tests/test_auth.py`, `tests/test_posts.py`, `tests/test_features.py`).

## Constraints Observed

- Every client-facing error path raises `HTTPException`; there are no bare `raise ValueError`/`raise RuntimeError` calls that escape to the framework layer (verified by grepping all `.py` files under `app/`).
- Auth failures consistently include `headers={"WWW-Authenticate": "Bearer"}` on 401 responses, conforming to RFC 7235.
- The optional-dependency pattern (`get_current_user_optional`) is the documented way to handle unauthenticated requests without try/except in endpoint bodies.