from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import api_router
from app.core.config import settings
from app.database import engine, Base

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.app_name)

# Update CORS to handle dynamic Vercel URLs
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOW_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app",  # Allow all Vercel deployments
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")

@app.get("/")
def read_root():
    return {"message": "Welcome to the Medium Clone API!"}

@app.post("/api/trigger-ai-bot")
async def trigger_ai_bot():
    """
    Endpoint to manually trigger AI bot content generation.
    Can be called by external cron services (e.g., cron-job.org, EasyCron)
    """
    try:
        import subprocess
        import os
        from pathlib import Path
        
        # Get the backend root directory (where ai_content_bot.py is)
        backend_dir = Path(__file__).parent.parent
        
        # Run the AI bot script
        result = subprocess.run(
            ["python", "ai_content_bot.py", "test"],
            cwd=str(backend_dir),
            capture_output=True,
            text=True,
            timeout=120
        )
        
        return {
            "status": "success" if result.returncode == 0 else "error",
            "message": "AI bot executed",
            "returncode": result.returncode,
            "stdout": result.stdout[:1000] if result.stdout else None,
            "stderr": result.stderr[:1000] if result.stderr else None
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)