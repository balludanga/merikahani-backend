# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# No compiler in the image: every pinned dependency ships a prebuilt wheel for 64-bit Linux
# (x86-64 and ARM), so nothing is built here. If a future pin has no wheel, the build fails
# loudly on that package, which is the moment to decide whether to add gcc back.

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies (the base image already ships a current pip)
RUN pip install -r requirements.txt

# The server runs as an ordinary user, not root: if the app were ever tricked into running
# something, it could not change the system or its own code. The code below stays owned by root
# (read-only to this user); the only place it can write is /app/data, where SQLite lives.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data \
    && chown app:app /app/data

# Copy application code
COPY . .

# A standalone `docker run` with no DATABASE_URL gets a SQLite file in the writable folder.
# Production sets its own DATABASE_URL; docker-compose.yml sets the same path explicitly.
ENV DATABASE_URL=sqlite:////app/data/app.db

# Numeric, so orchestrators that insist on "runAsNonRoot" can verify it without looking up a name.
USER 10001:10001

# Expose port
EXPOSE 8000

# Healthy = the root page answers. Python is already here, so no curl is needed. The first minute
# is a grace period for the start-up migrations.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/' % os.getenv('PORT', '8000'), timeout=4)"]

# Run as the image ships: no --reload (that is a development watcher, and it keeps a second
# process alive per container), and the port/process count come from the environment so a
# Render-style host gets what it asked for. Concurrent workers migrating at once is safe:
# app/migrations.py serialises startup behind a Postgres advisory lock.
# docker-compose.yml overrides this command for local development.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]
