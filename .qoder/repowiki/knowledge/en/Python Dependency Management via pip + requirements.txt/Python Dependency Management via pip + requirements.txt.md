---
kind: dependency_management
name: Python Dependency Management via pip + requirements.txt
category: dependency_management
scope:
    - '**'
source_files:
    - requirements.txt
    - Dockerfile
    - render.yaml
    - .gitignore
    - README.md
---

## Approach

The repository uses **pip** with a single flat `requirements.txt` manifest as its sole dependency declaration mechanism. There is no Poetry, Pipenv, `pyproject.toml`, lockfile, vendored packages, or private PyPI registry configured.

## Key Files

- `requirements.txt` — the only dependency manifest; declares all third-party packages and one pinned transitive (`bcrypt==4.0.1`).
- `Dockerfile` — installs dependencies at build time via `pip install -r requirements.txt`.
- `render.yaml` — Render deployment specifies `buildCommand: "pip install -r requirements.txt"`.
- `.gitignore` — ignores `.venv/`, `venv/`, `env/`, `*.egg-info/`, `dist/`, `build/`, `wheels/`, etc., so the virtual environment and any generated artifacts are never committed.
- `README.md` — documents `pip` as the required tool and instructs `pip install -r requirements.txt` for setup.

## How It Works

1. **Declaration**: All runtime dependencies (FastAPI, uvicorn, pydantic, sqlalchemy, psycopg2-binary, python-jose, passlib, bcrypt, httpx, requests, email-validator, python-dotenv, python-multipart) are listed by name in `requirements.txt`. Only `bcrypt` is pinned to an exact version (`==4.0.1`); every other package has no version specifier, meaning `pip install` resolves against the latest compatible release on PyPI at install time.
2. **Local development**: Developers create a virtual environment (`.venv/`) and run `pip install -r requirements.txt` from the repo root.
3. **Containerization**: The Docker image builds on `python:3.11-slim`, installs system build deps (`gcc`), then runs `pip install --upgrade pip && pip install -r requirements.txt` before copying application code.
4. **Cloud deployment**: Render's `render.yaml` reuses the same command (`pip install -r requirements.txt`) during its build step.

## Conventions & Constraints

- **Single manifest**: All Python dependencies are declared exclusively in `requirements.txt`; there is no `pyproject.toml`, `Pipfile`, or `poetry.lock`.
- **No lockfile**: No `requirements.lock`, `poetry.lock`, or equivalent is present, so dependency resolution is non-deterministic across environments unless the underlying PyPI index state happens to be identical.
- **Selective pinning**: Only `bcrypt==4.0.1` is pinned to an exact version; all other packages are unpinned (latest-resolved).
- **Virtual environments are local-only**: `.venv/` and `venv/` are gitignored; they must be recreated per machine/environment.
- **No private registry / vendor strategy**: No `--index-url`, `--extra-index-url`, `PIP_INDEX_URL`, `PIP_TRUSTED_HOST`, `PYPI_TOKEN`, or `vendor/` directory is configured — all packages come from the public PyPI.
- **Docker build reproducibility gap**: The Dockerfile does not cache `pip install` separately from the app source copy, and it upgrades pip at every build; combined with unpinned versions this means images can differ between builds.