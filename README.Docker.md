# Docker Setup for Meri Kahani Backend

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
- **Health Check**: http://localhost:8000/health

## Development with Hot Reload

The Docker setup includes hot reload by default:

1. Any changes to Python files will automatically restart the server
2. Database files are persisted via volumes
3. Code is mounted as a volume for live updates

## Running the AI Content Bot

Uncomment the `ai-bot` service in `docker-compose.yml`:

```yaml
ai-bot:
  build:
    context: .
    dockerfile: Dockerfile
  container_name: merikahani-ai-bot
  volumes:
    - .:/app
  environment:
    - NEWS_API_KEY=${NEWS_API_KEY}
  depends_on:
    - backend
  restart: unless-stopped
  command: python ai_scheduler.py
```

Then restart:
```bash
docker-compose up -d
```

## Environment Variables

Create a `.env` file in the backend directory:

```bash
NEWS_API_KEY=your_news_api_key_here
SECRET_KEY=your_secret_key_here
```

## Useful Commands

### View Running Containers
```bash
docker-compose ps
```

### Execute Commands in Container
```bash
# Open shell in backend container
docker-compose exec backend bash

# Run database migrations
docker-compose exec backend python -m alembic upgrade head

# Create AI bot user
docker-compose exec backend python ai_content_bot.py setup

# Generate a test article
docker-compose exec backend python ai_content_bot.py test
```

### Restart Services
```bash
# Restart all services
docker-compose restart

# Restart only backend
docker-compose restart backend
```

### Clean Up
```bash
# Stop and remove containers, networks
docker-compose down

# Remove volumes as well (WARNING: deletes database!)
docker-compose down -v

# Remove images
docker-compose down --rmi all
```

## Production Deployment

For production, modify `docker-compose.yml`:

1. Remove `--reload` flag from uvicorn command
2. Use production WSGI server (gunicorn)
3. Add environment-specific configs
4. Use secrets management for API keys

```yaml
command: gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

## Troubleshooting

### Port Already in Use
```bash
# Kill process on port 8000
lsof -ti:8000 | xargs kill -9

# Or use different port in docker-compose.yml
ports:
  - "8001:8000"
```

### Database Issues
```bash
# Reset database
rm app.db medium_clone.db
docker-compose restart backend
```

### View Logs
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f backend

# Last 100 lines
docker-compose logs --tail=100 backend
```

## Volume Management

Database files are persisted via Docker volumes:
- `./app.db` - Main application database
- `./medium_clone.db` - Legacy database

To backup:
```bash
docker-compose exec backend cp app.db /tmp/backup.db
docker cp merikahani-backend:/tmp/backup.db ./backup.db
```

## Network Configuration

Services communicate via the `merikahani-network` bridge network:
- Backend service: `backend:8000`
- AI Bot can access backend via: `http://backend:8000`
