# Docker Setup for लेखनी (Lekhani) Backend

## Quick Start

### 1. Build and Run with Docker Compose

```bash
# Build and start the backend
docker-compose up --build

# Run in background (detached mode)
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### 2. Access the Application

- **Backend API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **Sitemap**: http://localhost:8000/api/sitemap.xml
- **RSS**: http://localhost:8000/api/rss.xml

## Development with Hot Reload

`docker-compose.yml` is the development stack: it mounts the source tree and runs uvicorn with
`--reload`, and sets `DEBUG=true`. The image itself starts differently (no reload, one process per
`WEB_CONCURRENCY`) so it can be run standalone on a server.

1. Any changes to Python files will automatically restart the server
2. Database files are persisted in the `lekhani-db` volume
3. Code is mounted as a volume for live updates

## Database

The container keeps SQLite under `/app/data/app.db`, on a named Docker volume, so your data
survives rebuilds and restarts. (Mounting a single `app.db` file from the host instead is the
classic trap: if the file is missing, Docker creates a *directory* called `app.db` and SQLite
then refuses to open it.) For production, set `DATABASE_URL` to a PostgreSQL connection string.

To move an existing `app.db` from before this change:

```bash
docker compose cp ./app.db backend:/app/data/app.db
```

## Environment Variables

Copy `.env.example` to `.env` and adjust. The compose file sets `DEBUG=true` and a SQLite
`DATABASE_URL` of its own; values passed there win over `.env`, which is not copied into the
image (see `.dockerignore`) — supply real secrets with `env_file` or the shell:

- `DATABASE_URL` — database connection (defaults to SQLite)
- `SECRET_KEY` — JWT signing key (a private random value whenever `DEBUG` is off)
- `GOOGLE_CLIENT_ID` — Google sign-in client ID
- `FRONTEND_URL` — public URL of the Lekhani frontend (used for sitemap/RSS)
- `WEB_CONCURRENCY` / `PORT` — how many processes the image runs and where it listens

## Deploying to Render

`render.yaml` describes the web service only. There is **no** Render-managed database: the free
Postgres expires after 30 days, so production `DATABASE_URL` (the Supabase session pooler string)
is set by hand in the dashboard, and `sync: false` keeps a blueprint update from recreating an
empty database over it:

```bash
# In the Render dashboard: New + → Blueprint → select this repository
```

## Backup and Restore

```bash
# Backup the SQLite database from a running container
docker cp lekhani-backend:/app/data/app.db ./backup.db

# Restore
docker cp ./backup.db lekhani-backend:/app/data/app.db
docker compose restart
```
