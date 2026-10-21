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

The Docker setup includes hot reload by default:

1. Any changes to Python files will automatically restart the server
2. Database files are persisted via volumes
3. Code is mounted as a volume for live updates

## Database

Local development uses SQLite (`app.db`) mounted as a Docker volume, so your
data survives container restarts. For production, set `DATABASE_URL` to a
PostgreSQL connection string.

## Environment Variables

Copy `.env.example` to `.env` and adjust:

- `DATABASE_URL` — database connection (defaults to SQLite)
- `SECRET_KEY` — JWT signing key (set a secure random value in production)
- `GOOGLE_CLIENT_ID` — Google sign-in client ID
- `FRONTEND_URL` — public URL of the Lekhani frontend (used for sitemap/RSS)

## Deploying to Render

The included `render.yaml` blueprint provisions the web service and a
PostgreSQL database:

```bash
# In the Render dashboard: New + → Blueprint → select this repository
```

## Backup and Restore

```bash
# Backup the SQLite database from a running container
docker cp lekhani-backend:/app/app.db ./backup.db

# Restore
docker cp ./backup.db lekhani-backend:/app/app.db
docker-compose restart
```
