---
kind: build_system
name: Build & Deployment via Docker, docker-compose, and Render Blueprint
category: build_system
scope:
    - '**'
source_files:
    - Dockerfile
    - docker-compose.yml
    - render.yaml
    - requirements.txt
    - README.Docker.md
---

## Build System Overview

This repository is a Python FastAPI application with no custom build tooling (no `Makefile`, no `build.sh`, no CI pipeline files). The build and deployment surface consists of three artifacts:

1. **`requirements.txt`** — the single source of truth for Python dependencies.
2. **`Dockerfile`** — builds a container image from `python:3.11-slim`.
3. **`docker-compose.yml`** — local development orchestration with hot reload and volume-mounted SQLite.
4. **`render.yaml`** — Render.com blueprint that provisions a web service and a PostgreSQL database.

There is no version manifest (e.g., `pyproject.toml`), no lock file (`requirements.lock` / `Pipfile.lock`), no virtualenv creation script, no test runner wrapper, and no CI/CD configuration beyond the Render blueprint.

## Key Files

- `Dockerfile` — base image `python:3.11-slim`; installs system deps (`gcc`), then runs `pip install -r requirements.txt`, copies the whole repo into `/app`, exposes port 8000, and starts `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`.
- `docker-compose.yml` — defines a single `backend` service built from the root `Dockerfile`; mounts source code (`.` → `/app`) and `./app.db` as volumes; sets `PYTHONUNBUFFERED=1`; restarts `unless-stopped`; creates a bridge network named `lekhani-network`.
- `render.yaml` — declares a `web` service (`runtime: python`, `plan: free`) with `buildCommand: pip install -r requirements.txt` and `startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT`; also provisions a `postgresql` database named `lekhani-db` and wires its connection string to `DATABASE_URL`. Environment variables include `SECRET_KEY` (generated at deploy time), `DEBUG=false`, `GOOGLE_CLIENT_ID` (sync disabled), and `FRONTEND_URL=https://lekhani.vercel.app`.
- `requirements.txt` — pins all runtime dependencies; only one transitive dep is pinned explicitly (`bcrypt==4.0.1`).
- `README.Docker.md` — documents the compose workflow, hot-reload behavior, SQLite vs PostgreSQL usage, environment variables, and Render deployment steps.

## Architecture & Conventions

- **Container-first development**: Local dev uses `docker-compose up --build` (per `README.Docker.md`); the compose config mounts source code so changes trigger Uvicorn's `--reload` loop.
- **Dev vs prod DB**: Development defaults to SQLite (`app.db` mounted as a volume); production on Render uses the provisioned PostgreSQL instance via `DATABASE_URL`.
- **Single entrypoint**: Both Docker and Render invoke `uvicorn app.main:app` directly — there is no WSGI adapter layer or separate Gunicorn process.
- **Port convention**: The app listens on `8000` inside containers; Render injects `$PORT` at runtime for the start command.
- **No multi-stage build**: The Dockerfile is a single-stage image that includes both build-time (`gcc`) and runtime dependencies.
- **Hot reload enabled everywhere**: The Dockerfile CMD and the compose command both pass `--reload` to Uvicorn, even though this is intended for development.

## Conventions & Constraints

- Dependencies are declared exclusively in `requirements.txt`; both the Dockerfile and Render's `buildCommand` install from it verbatim.
- The container image is based on `python:3.11-slim` (Dockerfile) while Render is configured with `PYTHON_VERSION=3.9.6` (render.yaml) — these two versions differ.
- Only `bcrypt==4.0.1` is pinned by exact version in `requirements.txt`; all other packages use unpinned version specifiers.
- The Dockerfile sets `PIP_NO_CACHE_DIR=1`, `PYTHONUNBUFFERED=1`, and `PYTHONDONTWRITEBYTECODE=1` as environment variables.
- The Render blueprint auto-deploys on push (`autoDeploy: true`) and generates `SECRET_KEY` at deploy time (`generateValue: true`).
- There is no test execution step in any build or deployment artifact — tests under `tests/` are not invoked by Docker, compose, or Render.
- No CI pipeline (GitHub Actions, GitLab CI, etc.) exists in the repository tree.